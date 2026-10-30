create extension if not exists pgcrypto;

create table if not exists public.users (
  telegram_id text primary key,
  username text,
  full_name text,
  created_at timestamptz not null default now(),
  last_seen timestamptz not null default now()
);

create table if not exists public.wallets (
  telegram_id text primary key references public.users(telegram_id) on delete cascade,
  balance bigint not null default 0 check (balance >= 0),
  updated_at timestamptz not null default now()
);

create table if not exists public.transactions (
  id uuid primary key default gen_random_uuid(),
  telegram_id text not null references public.users(telegram_id),
  order_id text unique,
  amount bigint not null check (amount > 0),
  transaction_type text not null check (transaction_type in ('topup','debit','vip')),
  status text not null default 'pending',
  description text,
  created_at timestamptz not null default now()
);

create table if not exists public.vip_subscriptions (
  telegram_id text primary key references public.users(telegram_id) on delete cascade,
  expires_at timestamptz not null,
  daily_limit integer not null default 500,
  updated_at timestamptz not null default now()
);

create table if not exists public.banned_users (
  telegram_id text primary key,
  reason text,
  created_at timestamptz not null default now()
);

create table if not exists public.checks (
  id uuid primary key default gen_random_uuid(),
  telegram_id text not null references public.users(telegram_id),
  total integer not null,
  deliverable integer not null default 0,
  undeliverable integer not null default 0,
  risky integer not null default 0,
  api_error integer not null default 0,
  results jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now()
);

create or replace function public.ensure_wallet(p_user_id text)
returns void language plpgsql security definer set search_path = public as $$
begin
  insert into wallets(telegram_id) values (p_user_id) on conflict do nothing;
end; $$;

create or replace function public.debit_wallet(p_user_id text, p_amount bigint, p_description text)
returns boolean language plpgsql security definer set search_path = public as $$
declare affected integer;
begin
  if p_amount <= 0 then return false; end if;
  update wallets set balance = balance - p_amount, updated_at = now()
    where telegram_id = p_user_id and balance >= p_amount;
  get diagnostics affected = row_count;
  if affected = 0 then return false; end if;
  insert into transactions(telegram_id, amount, transaction_type, status, description)
    values (p_user_id, p_amount, 'debit', 'settlement', p_description);
  return true;
end; $$;

create or replace function public.complete_topup(p_order_id text, p_amount bigint, p_provider_status text)
returns boolean language plpgsql security definer set search_path = public as $$
declare tx transactions%rowtype;
begin
  select * into tx from transactions where order_id = p_order_id for update;
  if not found or tx.transaction_type <> 'topup' or tx.status <> 'pending' or tx.amount <> p_amount then
    return false;
  end if;
  update transactions set status = p_provider_status where id = tx.id;
  insert into wallets(telegram_id, balance) values (tx.telegram_id, p_amount)
    on conflict (telegram_id) do update set balance = wallets.balance + excluded.balance, updated_at = now();
  return true;
end; $$;

create or replace function public.activate_vip(p_user_id text, p_days integer, p_daily_limit integer)
returns void language plpgsql security definer set search_path = public as $$
begin
  insert into vip_subscriptions(telegram_id, expires_at, daily_limit)
  values (p_user_id, now() + make_interval(days => p_days), p_daily_limit)
  on conflict (telegram_id) do update
  set expires_at = greatest(vip_subscriptions.expires_at, now()) + make_interval(days => p_days),
      daily_limit = excluded.daily_limit, updated_at = now();
end; $$;

alter table public.users enable row level security;
alter table public.wallets enable row level security;
alter table public.transactions enable row level security;
alter table public.vip_subscriptions enable row level security;
alter table public.banned_users enable row level security;
alter table public.checks enable row level security;

-- Backend uses the Supabase service-role key; never expose it to browsers or users.
revoke all on function public.ensure_wallet(text) from public, anon, authenticated;
revoke all on function public.debit_wallet(text,bigint,text) from public, anon, authenticated;
revoke all on function public.complete_topup(text,bigint,text) from public, anon, authenticated;
revoke all on function public.activate_vip(text,integer,integer) from public, anon, authenticated;
grant execute on function public.ensure_wallet(text) to service_role;
grant execute on function public.debit_wallet(text,bigint,text) to service_role;
grant execute on function public.complete_topup(text,bigint,text) to service_role;
grant execute on function public.activate_vip(text,integer,integer) to service_role;
