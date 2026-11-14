-- Billing: a monthly Razorpay subscription, one free trial mission, metered talk time.

alter table conversations add column if not exists talk_seconds integer not null default 0;

alter table principals
  add column if not exists trial_mission_id uuid references missions(id) on delete set null;

create table if not exists subscriptions (
  principal_id uuid primary key references principals(id) on delete cascade,
  razorpay_subscription_id text not null unique,
  status text not null,
  current_start timestamptz,
  current_end timestamptz,
  cancel_at_period_end boolean not null default false,
  updated_at timestamptz not null default now()
);

alter table subscriptions enable row level security;
