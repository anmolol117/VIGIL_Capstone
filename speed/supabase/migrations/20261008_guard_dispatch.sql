-- Guard dispatch: when a speeding event is logged, notify the nearest on-duty
-- guards. Additive only -- this project is shared with the campus parking and
-- RFID systems, so nothing they use is altered.

-- pg_net is not relocatable, so install it straight into the extensions schema
-- (its functions always live in the "net" schema regardless).
create extension if not exists pg_net with schema extensions;

-- ---------------------------------------------------------------------------
-- Event location and claim
-- ---------------------------------------------------------------------------
alter table public.speeding_events
  add column if not exists lat double precision,
  add column if not exists lon double precision,
  add column if not exists claimed_by uuid references auth.users (id) on delete set null,
  add column if not exists claimed_by_name text,
  add column if not exists claimed_at timestamptz;

comment on column public.speeding_events.lat is
  'Fixed hover position of the drone that logged the event. Vehicles are directly below it, within a footprint of tens of metres, so this is the event location for dispatch.';
comment on column public.speeding_events.claimed_by is
  'First guard to respond. Set once, atomically, by respond_to_event().';

-- ---------------------------------------------------------------------------
-- Guards
-- ---------------------------------------------------------------------------
create table public.guards (
  id          uuid primary key references auth.users (id) on delete cascade,
  name        text not null,
  phone       text,
  on_duty     boolean not null default false,
  lat         double precision,
  lon         double precision,
  location_at timestamptz,
  fcm_token   text,
  platform    text check (platform in ('android', 'ios')),
  created_at  timestamptz not null default now()
);
comment on table public.guards is
  'Guards who receive speeding alerts. A row here, not authentication alone, grants guard access: auth users are shared with the campus parking app. Only the latest position is kept, and only while on duty.';

-- ---------------------------------------------------------------------------
-- Dispatches: one row per (event, notified guard)
-- ---------------------------------------------------------------------------
create table public.dispatches (
  id           bigint generated always as identity primary key,
  event_id     bigint not null references public.speeding_events (id) on delete cascade,
  guard_id     uuid   not null references public.guards (id) on delete cascade,
  distance_m   real   not null,
  bearing_deg  real   not null,
  status       text   not null default 'sent' check (status in ('sent', 'responding', 'declined')),
  sent_at      timestamptz not null default now(),
  push_sent_at timestamptz,
  delivered_at timestamptz,
  opened_at    timestamptz,
  responded_at timestamptz,
  unique (event_id, guard_id)
);
comment on table public.dispatches is
  'Who was notified about which event. distance/bearing are from the guard to the event at dispatch time. delivered_at/opened_at are reported by the app, so end-to-end latency is measured, not estimated.';
create index dispatches_guard_time_idx on public.dispatches (guard_id, sent_at desc);

-- ---------------------------------------------------------------------------
-- Geometry: great-circle distance and initial bearing, point 1 -> point 2
-- ---------------------------------------------------------------------------
create or replace function public.gc_distance_m(lat1 float8, lon1 float8, lat2 float8, lon2 float8)
returns float8 language sql immutable set search_path = '' as $$
  select 2 * 6371008.8 * asin(sqrt(
           power(sin(radians(lat2 - lat1) / 2), 2) +
           cos(radians(lat1)) * cos(radians(lat2)) * power(sin(radians(lon2 - lon1) / 2), 2)))
$$;

create or replace function public.gc_bearing_deg(lat1 float8, lon1 float8, lat2 float8, lon2 float8)
returns float8 language sql immutable set search_path = '' as $$
  select case when b < 0 then b + 360 else b end
  from (select degrees(atan2(
          sin(radians(lon2 - lon1)) * cos(radians(lat2)),
          cos(radians(lat1)) * sin(radians(lat2)) -
          sin(radians(lat1)) * cos(radians(lat2)) * cos(radians(lon2 - lon1)))) as b) s
$$;

-- ---------------------------------------------------------------------------
-- Dispatch on insert. Runs inside the pipeline's INSERT, so dispatch rows exist
-- (and reach open apps over Realtime) the moment the event does; the edge
-- function only adds the push for closed apps. ON CONFLICT DO UPDATE (peak
-- updates) does not fire INSERT triggers, so each vehicle dispatches once.
-- ---------------------------------------------------------------------------
create or replace function public.dispatch_speeding_event()
returns trigger language plpgsql security definer set search_path = '' as $$
declare
  n int;
begin
  if new.lat is null or new.lon is null then
    return new;
  end if;
  begin
    -- ponytail: full scan over guards; fine for hundreds. Add a spatial index if this grows to thousands.
    insert into public.dispatches (event_id, guard_id, distance_m, bearing_deg)
    select new.id, g.id,
           public.gc_distance_m(g.lat, g.lon, new.lat, new.lon),
           public.gc_bearing_deg(g.lat, g.lon, new.lat, new.lon)
    from public.guards g
    where g.on_duty
      and g.lat is not null and g.lon is not null
      and g.location_at > now() - interval '30 minutes'   -- position must be recent
    order by public.gc_distance_m(g.lat, g.lon, new.lat, new.lon)
    limit 3;                                              -- nearest 3 guards
    get diagnostics n = row_count;

    if n > 0 then
      perform net.http_post(
        url := 'https://grknwhtsoginetxkmeto.supabase.co/functions/v1/dispatch',
        body := jsonb_build_object('event_id', new.id),
        headers := '{"Content-Type": "application/json"}'::jsonb,
        timeout_milliseconds := 5000);
    end if;
  exception when others then
    -- Never lose the event because dispatch failed.
    raise warning 'dispatch failed for speeding event %: %', new.id, sqlerrm;
  end;
  return new;
end $$;

create trigger speeding_events_dispatch
  after insert on public.speeding_events
  for each row execute function public.dispatch_speeding_event();

-- ---------------------------------------------------------------------------
-- Push batch for the edge function. Marks rows as pushed in the same statement,
-- so repeated or spoofed calls are no-ops.
-- ---------------------------------------------------------------------------
create or replace function public.claim_push_batch(p_event_id bigint)
returns table (dispatch_id bigint, fcm_token text, distance_m real, bearing_deg real,
               vehicle_type text, speed_kmh real, speed_limit real, camera text,
               lat float8, lon float8, detected_at timestamptz)
language sql security definer set search_path = '' as $$
  with claimed as (
    update public.dispatches d set push_sent_at = now()
    where d.event_id = p_event_id and d.push_sent_at is null
    returning d.id, d.guard_id, d.distance_m, d.bearing_deg
  )
  select c.id, g.fcm_token, c.distance_m, c.bearing_deg,
         e.vehicle_type, e.speed_kmh, e.speed_limit, e.camera, e.lat, e.lon, e.detected_at
  from claimed c
  join public.guards g on g.id = c.guard_id
  join public.speeding_events e on e.id = p_event_id
  where g.fcm_token is not null
$$;

-- ---------------------------------------------------------------------------
-- Respond. First responder wins: a conditional single-row UPDATE is atomic, so
-- two guards tapping at once cannot both claim the event.
-- Returns 'responding', 'declined', or 'taken:<name>'.
-- ---------------------------------------------------------------------------
create or replace function public.respond_to_event(p_event_id bigint, p_action text)
returns text language plpgsql security definer set search_path = '' as $$
declare
  me uuid := auth.uid();
  my_name text;
  holder uuid;
  holder_name text;
begin
  if p_action not in ('responding', 'declined') then
    raise exception 'invalid action %', p_action;
  end if;

  select g.name into my_name
  from public.guards g
  join public.dispatches d on d.guard_id = g.id and d.event_id = p_event_id
  where g.id = me;
  if my_name is null then
    raise exception 'not dispatched to this event';
  end if;

  if p_action = 'declined' then
    update public.dispatches set status = 'declined', responded_at = now()
    where event_id = p_event_id and guard_id = me and status = 'sent';
    return 'declined';
  end if;

  update public.speeding_events
  set claimed_by = me, claimed_by_name = my_name, claimed_at = now()
  where id = p_event_id and claimed_by is null;

  if not found then
    select claimed_by, claimed_by_name into holder, holder_name
    from public.speeding_events where id = p_event_id;
    if holder is distinct from me then
      return 'taken:' || coalesce(holder_name, '');
    end if;
  end if;

  update public.dispatches set status = 'responding', responded_at = now()
  where event_id = p_event_id and guard_id = me;
  return 'responding';
end $$;

-- ---------------------------------------------------------------------------
-- Access. Guards see and change only their own rows; everything else goes
-- through the functions above.
-- ---------------------------------------------------------------------------
alter table public.guards enable row level security;
alter table public.dispatches enable row level security;

revoke all on public.guards, public.dispatches from anon, authenticated;
grant select on public.guards, public.dispatches to authenticated;
grant update (on_duty, lat, lon, location_at, fcm_token, platform) on public.guards to authenticated;
grant update (delivered_at, opened_at) on public.dispatches to authenticated;

create policy "guard reads own row" on public.guards
  for select to authenticated using (id = auth.uid());
create policy "guard updates own row" on public.guards
  for update to authenticated using (id = auth.uid()) with check (id = auth.uid());

create policy "guard reads own dispatches" on public.dispatches
  for select to authenticated using (guard_id = auth.uid());
create policy "guard updates own dispatch receipts" on public.dispatches
  for update to authenticated using (guard_id = auth.uid()) with check (guard_id = auth.uid());

create policy "guard reads events dispatched to them" on public.speeding_events
  for select to authenticated using (
    exists (select 1 from public.dispatches d
            where d.event_id = speeding_events.id and d.guard_id = auth.uid()));

revoke execute on function public.claim_push_batch(bigint) from public, anon, authenticated;
grant execute on function public.claim_push_batch(bigint) to service_role;
revoke execute on function public.respond_to_event(bigint, text) from public, anon;
grant execute on function public.respond_to_event(bigint, text) to authenticated;
revoke execute on function public.dispatch_speeding_event() from public, anon, authenticated;

-- Live updates to open apps: new dispatches, and claims on events.
alter publication supabase_realtime add table public.dispatches, public.speeding_events;
