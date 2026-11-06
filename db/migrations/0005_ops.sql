-- Faza 5: predlozi novih izvora (agent 2) i stanje popravki adaptera (agent 1).

create table source_proposals (
  id          bigserial primary key,
  name        text not null,
  url         text not null,
  payload     jsonb not null,
  status      text not null default 'pending'
              check (status in ('pending', 'accepted', 'rejected', 'added')),
  created_at  timestamptz not null default now(),
  resolved_at timestamptz
);

create index on source_proposals (status, created_at desc);

grant select on source_proposals to agent_reader;
