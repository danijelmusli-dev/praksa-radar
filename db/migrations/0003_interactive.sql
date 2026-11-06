-- Faza 3: tracker, podsetnici, potvrde izmena, trag agenata, uloge sa najmanjim privilegijama.

-- Worker upisuje nove verzije profila bez embeddinga; pipeline ga dopuni pre ranga.
alter table profile_versions alter column embedding drop not null;

create table applications (
  opportunity_id bigint primary key references opportunities(id),
  status         text not null
                 check (status in ('sačuvano', 'prijavljen', 'intervju', 'ponuda', 'odbijen', 'odustao')),
  applied_at     timestamptz,
  next_action_at timestamptz,
  notes          text,
  updated_at     timestamptz not null default now()
);

create table reminders (
  id             bigserial primary key,
  opportunity_id bigint references opportunities(id),
  kind           text not null default 'manual',
  due_at         timestamptz not null,
  text           text not null,
  sent_at        timestamptz,
  created_at     timestamptz not null default now(),
  unique (opportunity_id, kind, due_at)
);

create index on reminders (due_at) where sent_at is null;

-- Izmene koje predloži LLM (profil) čekaju moje dugme.
create table pending_changes (
  id          bigserial primary key,
  kind        text not null,
  payload     jsonb not null,
  status      text not null default 'pending' check (status in ('pending', 'applied', 'rejected')),
  created_at  timestamptz not null default now(),
  resolved_at timestamptz
);

create table agent_runs (
  id            bigserial primary key,
  agent         text not null,
  started_at    timestamptz not null default now(),
  finished_at   timestamptz,
  steps         integer not null default 0,
  input_tokens  integer not null default 0,
  output_tokens integer not null default 0,
  cost_usd      numeric(10, 4),
  outcome       text,
  trace         jsonb not null default '[]'
);

-- Uloge: Worker se prijavljuje jednim korisnikom i u transakciji radi `set local role ...`.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'agent_reader') then
    create role agent_reader nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'agent_tracker') then
    create role agent_tracker nologin;
  end if;
end $$;

grant usage on schema public to agent_reader, agent_tracker;

-- agent_reader: samo čitanje (bez kv_state); niko od agenata ne briše.
grant select on opportunities, scores, profile_versions, applications, reminders,
  feedback, pending_changes, source_runs, agent_runs to agent_reader;

-- agent_tracker: piše samo u applications i reminders.
grant select on opportunities, applications, reminders to agent_tracker;
grant insert, update on applications, reminders to agent_tracker;
grant usage, select on sequence reminders_id_seq to agent_tracker;

grant agent_reader, agent_tracker to current_user;
