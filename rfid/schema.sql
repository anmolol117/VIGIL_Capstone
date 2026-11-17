-- Applied to project grknwhtsoginetxkmeto. Kept here so the schema is in
-- version control. Migrations: vehicle_logging_schema, store_direction_with_cooldown,
-- pin_direction_function_search_path, two_card_direction.

-- Registry: one row per vehicle. Cards live in `tags`, not here -- a vehicle
-- has two of them (entry and exit), and may be registered before either is
-- issued.
create table public.vehicles (
  id            uuid primary key default gen_random_uuid(),
  plate_number  text not null unique,
  vehicle_type  text not null check (vehicle_type in
                  ('car','motorcycle','scooter','bicycle','bus','truck','other')),
  vehicle_name  text,
  owner_name    text not null,
  owner_role    text not null check (owner_role in
                  ('professor','staff','student','guard','visitor','contractor')),
  active        boolean not null default true,
  registered_at timestamptz not null default now()
);

-- Two cards per vehicle: tapping the entry card means "in", the exit card
-- means "out". Direction is therefore DATA, not inference.
--
-- Why not derive it from scan order? Because parity is chained state: one
-- missed or spurious scan inverts that card forever, and every later row
-- inherits the error. With a card per direction, each scan is independent --
-- a wrong tap is exactly one wrong row and the next scan is still correct.
--
-- Why not use which gate saw the card? Gate roles here change with university
-- policy, so device_id is not a reliable direction signal.
create table public.tags (
  rfid_uid   text primary key,
  vehicle_id uuid not null references public.vehicles(id) on delete cascade,
  direction  text not null check (direction in ('in','out')),
  issued_at  timestamptz not null default now()
);
create index tags_vehicle_id_idx on public.tags (vehicle_id);

-- One row per scan. rfid_uid is always stored raw so an unknown card still
-- produces a record; vehicle_id and direction are null when the card is
-- unregistered, because its direction is then genuinely unknown.
create table public.vehicle_logs (
  id         bigint generated always as identity primary key,
  device_id  int    not null,
  counter    bigint not null,
  rfid_uid   text   not null,
  vehicle_id uuid   references public.vehicles(id),
  direction  text   check (direction in ('in','out')),
  is_reread  boolean not null default false,
  rssi       int,
  snr        real,
  logged_at  timestamptz not null default now(),
  -- Idempotency backstop. If the DB write succeeds but the ACK is lost coming
  -- back, the transmitter retries the same counter and hits this instead of
  -- creating a duplicate row. The gateway maps 409 -> ACK status 0x01.
  unique (device_id, counter)
);

create index vehicle_logs_rfid_uid_logged_at_idx on public.vehicle_logs (rfid_uid, logged_at desc);
create index vehicle_logs_vehicle_id_logged_at_idx on public.vehicle_logs (vehicle_id, logged_at desc);

-- The trigger does not decide direction -- the card does. All it does is flag
-- duplicates: a car idling at the barrier re-reads the same card, and those
-- should be visible rather than looking like repeated entries.
create function public.vehicle_logs_flag_reread()
returns trigger
language plpgsql
set search_path = ''          -- pinned: no caller-controlled schema resolution
as $$
declare
  cooldown constant interval := interval '60 seconds';   -- tunable
  prev_at  timestamptz;
begin
  select l.logged_at into prev_at
    from public.vehicle_logs l
   where l.rfid_uid = new.rfid_uid
     and l.logged_at <= new.logged_at
   order by l.logged_at desc
   limit 1;

  if prev_at is not null and new.logged_at - prev_at < cooldown then
    new.is_reread := true;
  end if;
  return new;
end $$;

create trigger vehicle_logs_reread
  before insert on public.vehicle_logs
  for each row execute function public.vehicle_logs_flag_reread();

-- RLS on with no policies: service_role bypasses it, anon/authenticated get
-- nothing. vehicles holds real names and plate numbers.
alter table public.vehicles     enable row level security;
alter table public.vehicle_logs enable row level security;
alter table public.tags         enable row level security;

-- The view a dashboard reads.
create view public.vehicle_movements
with (security_invoker = true) as
select l.id, l.logged_at, l.rfid_uid, l.device_id, l.rssi, l.snr,
       l.direction, l.is_reread,
       v.plate_number, v.vehicle_type, v.vehicle_name, v.owner_name, v.owner_role
from public.vehicle_logs l
left join public.vehicles v on v.id = l.vehicle_id;
