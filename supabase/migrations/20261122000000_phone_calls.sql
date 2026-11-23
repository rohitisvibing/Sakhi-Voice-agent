-- Phone calls (Exotel): a third conversation mode, the callee, and the call's status.
alter table conversations drop constraint if exists conversations_mode_check;
alter table conversations add constraint conversations_mode_check
  check (mode in ('simulated', 'live', 'phone'));

alter table conversations add column if not exists recipient_name text;
alter table conversations add column if not exists recipient_phone text;
alter table conversations add column if not exists call_sid text;
alter table conversations add column if not exists call_status text;
create index if not exists conversations_call_sid_idx on conversations (call_sid);

-- The browser can reach a different server than the one holding the call: controls
-- (end, nudges, approval decisions) are queued here and picked up by the call's server.
create table if not exists call_commands (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references conversations (id) on delete cascade,
  type text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  consumed_at timestamptz
);
create index if not exists call_commands_pending_idx
  on call_commands (conversation_id) where consumed_at is null;
alter table call_commands enable row level security;  -- service role only
