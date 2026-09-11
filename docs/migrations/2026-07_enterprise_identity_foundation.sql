-- ============================================================================
-- Enterprise identity, saved instances, and shared secret custody (July 2026)
-- ============================================================================
-- Establishes the durable model used by OIDC/SAML/SCIM, device sessions, and
-- the shared encrypted vault. Protocol handlers arrive in later migrations;
-- this migration is additive and safe to apply before those surfaces ship.
-- ============================================================================

create table if not exists organizations (
  id          text primary key,
  name        text not null,
  created_at  timestamptz not null default now()
);

insert into organizations (id, name)
values ('default', 'Default')
on conflict (id) do nothing;

insert into organizations (id, name)
select org_id, org_id
from (
  select distinct coalesce(org_id, 'default') as org_id from agent_registry
  union
  select distinct coalesce(org_id, 'default') as org_id from agent_jobs
  union
  select distinct coalesce(org_id, 'default') as org_id from llm_connections
) existing_orgs
where org_id is not null
on conflict (id) do nothing;

create table if not exists secret_records (
  id          uuid primary key,
  org_id      text not null references organizations(id) on delete cascade,
  scope       text not null,
  name        text not null,
  ciphertext  text not null,
  nonce       text not null,
  key_version integer not null default 1,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  unique (org_id, scope, name)
);

create table if not exists users (
  id            uuid primary key default gen_random_uuid(),
  email         text not null,
  display_name  text,
  active        boolean not null default true,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  unique (email)
);

create table if not exists saved_instances (
  id           uuid primary key default gen_random_uuid(),
  org_id       text not null references organizations(id) on delete cascade,
  name         text not null,
  slug         text not null,
  gateway_url  text,
  mode         text not null default 'hosted'
               check (mode in ('local', 'hosted', 'self_hosted')),
  active       boolean not null default true,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  unique (org_id, slug)
);

create table if not exists identity_providers (
  id             uuid primary key default gen_random_uuid(),
  org_id         text not null references organizations(id) on delete cascade,
  name           text not null,
  protocol       text not null
                 check (protocol in ('oidc', 'saml', 'trusted_header', 'ldaps')),
  issuer         text,
  client_id      text,
  secret_ref     uuid references secret_records(id) on delete set null,
  enabled        boolean not null default true,
  jit_enabled    boolean not null default true,
  config         jsonb not null default '{}'::jsonb,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  unique (org_id, name)
);

create table if not exists external_identities (
  id                   uuid primary key default gen_random_uuid(),
  user_id              uuid not null references users(id) on delete cascade,
  identity_provider_id uuid not null references identity_providers(id) on delete cascade,
  subject              text not null,
  last_login_at        timestamptz,
  created_at           timestamptz not null default now(),
  unique (identity_provider_id, subject)
);

create table if not exists org_memberships (
  id           uuid primary key default gen_random_uuid(),
  org_id       text not null references organizations(id) on delete cascade,
  user_id      uuid not null references users(id) on delete cascade,
  role         text not null default 'viewer',
  scopes       jsonb,
  active       boolean not null default true,
  scim_external_id text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  unique (org_id, user_id)
);

create table if not exists role_mappings (
  id                   uuid primary key default gen_random_uuid(),
  org_id               text not null references organizations(id) on delete cascade,
  identity_provider_id uuid not null references identity_providers(id) on delete cascade,
  external_group       text not null,
  role                 text not null,
  scopes               jsonb,
  created_at           timestamptz not null default now(),
  unique (identity_provider_id, external_group)
);

create table if not exists user_sessions (
  id               uuid primary key default gen_random_uuid(),
  org_id           text not null references organizations(id) on delete cascade,
  user_id          uuid not null references users(id) on delete cascade,
  instance_id      uuid references saved_instances(id) on delete cascade,
  session_token_hash text not null unique,
  device_name      text,
  ip_address       inet,
  user_agent       text,
  created_at       timestamptz not null default now(),
  last_seen_at     timestamptz not null default now(),
  expires_at       timestamptz not null,
  revoked_at       timestamptz
);

create table if not exists oidc_transactions (
  id          text primary key,
  value       text not null,
  expires_at  timestamptz not null,
  created_at  timestamptz not null default now()
);

create table if not exists device_credentials (
  id           uuid primary key default gen_random_uuid(),
  org_id       text not null references organizations(id) on delete cascade,
  user_id      uuid references users(id) on delete cascade,
  instance_id  uuid references saved_instances(id) on delete cascade,
  name         text not null,
  token_hash   text not null,
  scopes       jsonb not null default '[]'::jsonb,
  created_at   timestamptz not null default now(),
  last_used_at timestamptz,
  expires_at   timestamptz,
  revoked_at   timestamptz,
  unique (token_hash)
);

create table if not exists security_events (
  id          bigint generated always as identity primary key,
  org_id      text not null references organizations(id) on delete cascade,
  user_id     uuid references users(id) on delete set null,
  event       text not null,
  outcome     text not null,
  metadata    jsonb not null default '{}'::jsonb,
  occurred_at timestamptz not null default now()
);

alter table llm_connections
  add column if not exists secret_ref uuid references secret_records(id) on delete set null;

create index if not exists idx_saved_instances_org on saved_instances (org_id);
create unique index if not exists idx_users_email_lower on users (lower(email));
create index if not exists idx_memberships_user on org_memberships (user_id);
create index if not exists idx_sessions_user_active on user_sessions (user_id, revoked_at, expires_at);
create index if not exists idx_oidc_transactions_expiry on oidc_transactions (expires_at);
create index if not exists idx_device_credentials_org on device_credentials (org_id, revoked_at);
create index if not exists idx_secret_records_org on secret_records (org_id, scope);
create index if not exists idx_security_events_org_time on security_events (org_id, occurred_at desc);
