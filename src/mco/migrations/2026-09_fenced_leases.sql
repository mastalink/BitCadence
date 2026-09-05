-- 0.4.0: serialize ownership and admission in the database; persist evidence
-- with every state mutation. Application service credentials remain trusted.
create extension if not exists pgcrypto;
alter table agent_jobs add column if not exists lease_id text;
alter table agent_jobs add column if not exists lease_epoch bigint;
alter table agent_jobs add column if not exists lease_incarnation text;
alter table agent_jobs add column if not exists lease_expires_at timestamptz;
create table if not exists mco_store_identity (id text primary key, incarnation text not null, created_at timestamptz default now());
insert into mco_store_identity(id, incarnation) values ('store', gen_random_uuid()::text) on conflict do nothing;
create table if not exists mco_control (id text primary key, paused boolean not null default false);
insert into mco_control(id) values ('governance') on conflict do nothing;
create table if not exists mco_audit_outbox (
  id uuid primary key default gen_random_uuid(), job_id text not null,
  event text not null, detail jsonb not null, org_id text default 'default',
  created_at timestamptz not null default clock_timestamp()
);
create or replace function mco_state_evidence() returns trigger language plpgsql as $$
begin
  insert into mco_audit_outbox(job_id,event,detail,org_id)
  values (coalesce(new.id,old.id)::text, 'state_committed',
          jsonb_build_object('operation',tg_op,'before',to_jsonb(old),'after',to_jsonb(new)),
          coalesce(new.org_id,old.org_id,'default'));
  return coalesce(new,old);
end $$;
drop trigger if exists mco_state_evidence on agent_jobs;
create trigger mco_state_evidence after insert or update or delete on agent_jobs
for each row execute function mco_state_evidence();
drop trigger if exists mco_outbox_immutable on mco_audit_outbox;
create trigger mco_outbox_immutable before update or delete on mco_audit_outbox
for each row execute function mco_audit_block_mutation();

create table if not exists mco_attempt_receipts (id text primary key, claim jsonb not null, updates jsonb not null, job jsonb not null);
create or replace function mco_lease(p_action text, p_job_id text default '', p_owner text default '',
 p_claim jsonb default '{}', p_updates jsonb default '{}', p_ttl integer default 900)
returns jsonb language plpgsql as $$
declare
 j agent_jobs%rowtype;
 inc text;
 paused_now boolean;
 t timestamptz;
 halted_rows jsonb;
 receipt mco_attempt_receipts%rowtype;
begin
 -- Shared global admission barrier: pause and acquire cannot pass each other.
 select paused into paused_now from mco_control where id='governance' for update;
 select incarnation into inc from mco_store_identity where id='store' for update;
 t := clock_timestamp();
 if p_action='identity' then return jsonb_build_object('incarnation',inc); end if;
 if p_action='rotate' then
   inc := gen_random_uuid()::text;
   update mco_store_identity set incarnation=inc, created_at=t where id='store';
   return jsonb_build_object('incarnation',inc);
 end if;
 if p_action in ('pause','resume') then
   update mco_control set paused=(p_action='pause') where id='governance';
   if p_action='pause' then
     with halted as (update agent_jobs set status='halted', lease_epoch=coalesce(lease_epoch,0)+1,
         error_message='Halted by operator kill switch' where status in ('leased','in_progress') returning *)
     select coalesce(jsonb_agg(to_jsonb(halted)), '[]'::jsonb) into halted_rows from halted;
   end if;
   return jsonb_build_object('halted',coalesce(halted_rows,'[]'::jsonb));
 end if;
 if p_action='update' and p_claim->>'lease_id' is not null then
   select * into receipt from mco_attempt_receipts where id=p_claim->>'lease_id';
   if found then
     if receipt.claim=p_claim and receipt.updates=p_updates and receipt.job->>'id'=p_job_id
        and p_owner=p_claim->>'agent_instance_id' then
       return jsonb_build_object('job',receipt.job || '{"_replayed":true}'::jsonb);
     end if;
     return jsonb_build_object('error','attempt already reported a different result');
   end if;
 end if;
 select * into j from agent_jobs where id::text=p_job_id for update;
 if not found then return jsonb_build_object('error','job not found'); end if;
 if p_action='acquire' then
   if paused_now or j.status<>'pending' or p_owner='' then return '{}'::jsonb; end if;
   update agent_jobs set status='leased',leased_by_instance_id=p_owner, started_at=t, completed_at=null,
     lease_id=gen_random_uuid()::text, lease_epoch=coalesce(lease_epoch,0)+1,lease_incarnation=inc,
     lease_expires_at=t+make_interval(secs=>greatest(1,p_ttl)) where id=j.id returning * into j;
 elsif p_action='expire' then
   if j.status not in ('leased','in_progress') or
      coalesce(j.lease_expires_at,j.started_at+make_interval(secs=>p_ttl))>t or
      coalesce(j.lease_expires_at,j.started_at) is null or
      to_jsonb(j)->'lease_id' is distinct from p_claim->'lease_id' or
      to_jsonb(j)->'lease_epoch' is distinct from p_claim->'lease_epoch' then return '{}'::jsonb; end if;
   update agent_jobs set status='pending',leased_by_instance_id=null, started_at=null,
      lease_id=null,lease_expires_at=null,lease_epoch=coalesce(lease_epoch,0)+1 where id=j.id returning * into j;
 elsif p_action in ('renew','update') then
   if p_owner='' or j.leased_by_instance_id is distinct from p_owner then
      return jsonb_build_object('error','not the lease holder'); end if;
   if j.lease_id is not null and
      (j.lease_id is distinct from p_claim->>'lease_id' or
       j.lease_epoch::text is distinct from p_claim->>'lease_epoch' or
       j.lease_incarnation is distinct from p_claim->>'lease_incarnation' or j.lease_incarnation is distinct from inc) then
      return jsonb_build_object('error','stale lease: this attempt no longer owns the job'); end if;
   if paused_now or j.status not in ('leased','in_progress') or
      coalesce(j.lease_expires_at,j.started_at+interval '900 seconds')<=t then
      return jsonb_build_object('error','stale lease: expired, halted, or terminal'); end if;
   if p_action='renew' then
     update agent_jobs set lease_expires_at=t+make_interval(secs=>greatest(1,p_ttl)) where id=j.id returning * into j;
   else
     if coalesce(p_updates->>'status','') not in ('in_progress','completed','failed') then
       return jsonb_build_object('error','illegal transition'); end if;
     update agent_jobs set status=p_updates->>'status',
       output_payload=case when p_updates ? 'output_payload' then p_updates->'output_payload' else output_payload end,
       error_message=case when p_updates ? 'error_message' then p_updates->>'error_message' else error_message end,
       completed_at=case when p_updates->>'status'='completed' then t else completed_at end
       where id=j.id returning * into j;
   end if;
 else return jsonb_build_object('error','unknown lease action');
 end if;
 if p_action='update' and j.status in ('completed','failed') and j.lease_id is not null then
   insert into mco_attempt_receipts values(j.lease_id,p_claim,p_updates,to_jsonb(j));
 end if;
 return jsonb_build_object('job',to_jsonb(j));
end $$;

-- Disable the former unfenced RPC. Gateways/workers must upgrade together.
create or replace function lease_task(p_agent_instance_id text,p_task_id text)
returns boolean language plpgsql as $$ begin
 raise exception 'Use mco_lease and retain the returned attempt proof';
end $$;

alter table agent_job_events add column if not exists outbox_id text;
alter table agent_job_events add column if not exists canonical_content text;
create unique index if not exists mco_event_outbox_once on agent_job_events(outbox_id) where outbox_id is not null;
create or replace function mco_append_event(p_content text,p_key text default null,p_outbox_id text default null)
returns boolean language plpgsql as $$
declare c jsonb; prev text; h text; sig text; stamp text;
begin
 c:=p_content::jsonb;
 perform pg_advisory_xact_lock(hashtextextended('mco.audit.' || (c->>'job_id'),0));
 if p_outbox_id is not null and exists(select 1 from agent_job_events where outbox_id=p_outbox_id) then return true; end if;
 select hash into prev from agent_job_events where job_id=c->>'job_id' order by created_at desc,id desc limit 1;
 prev:=coalesce(prev,'');
 stamp:=to_char(clock_timestamp() at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US') || '+00:00';
 p_content:=replace(p_content,c->>'created_at',stamp);
 c:=p_content::jsonb;
 h:=encode(digest(convert_to(prev || E'\n' || p_content,'UTF8'),'sha256'),'hex');
 if p_key is not null then sig:=encode(hmac(convert_to(h,'UTF8'),convert_to(p_key,'UTF8'),'sha256'),'hex'); end if;
 insert into agent_job_events(job_id,event,actor_id,actor_role,detail,created_at,prev_hash,hash,signature,outbox_id,canonical_content)
 values(c->>'job_id',c->>'event',c->>'actor_id',c->>'actor_role',c->'detail',(c->>'created_at')::timestamptz,prev,h,sig,p_outbox_id,p_content);
 return true;
end $$;
