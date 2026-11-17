-- Faster in-app alerts. postgres_changes delivered dispatches in 0.4-3.3 s
-- (median 1.1 s, measured): it polls the WAL in batches and authorises every
-- change per subscriber. A broadcast sent from the trigger goes straight to the
-- guard's private channel instead. The postgres_changes path stays in the app
-- as a fallback; duplicates are ignored there.

-- A guard may only receive broadcasts on their own topic.
create policy "guard receives own alerts" on realtime.messages
  for select to authenticated
  using (realtime.topic() = 'guard:' || (select auth.uid())::text);

create or replace function public.dispatch_speeding_event()
returns trigger language plpgsql security definer set search_path = '' as $$
declare
  r record;
  n int := 0;
begin
  if new.lat is null or new.lon is null then
    return new;
  end if;
  begin
    -- ponytail: full scan over guards; fine for hundreds. Add a spatial index if this grows to thousands.
    for r in
      insert into public.dispatches (event_id, guard_id, distance_m, bearing_deg)
      select new.id, g.id,
             public.gc_distance_m(g.lat, g.lon, new.lat, new.lon),
             public.gc_bearing_deg(g.lat, g.lon, new.lat, new.lon)
      from public.guards g
      where g.on_duty
        and g.lat is not null and g.lon is not null
        and g.location_at > now() - interval '30 minutes'   -- position must be recent
      order by public.gc_distance_m(g.lat, g.lon, new.lat, new.lon)
      limit 3                                               -- nearest 3 guards
      returning id, guard_id
    loop
      -- Delivered on commit, straight to that guard's open app.
      perform realtime.send(
        jsonb_build_object('event_id', new.id, 'dispatch_id', r.id),
        'alert', 'guard:' || r.guard_id::text, true);
      n := n + 1;
    end loop;

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
