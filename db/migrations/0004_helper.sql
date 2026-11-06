-- Faza 4: moji projekti, nacrti/analize, beleške o firmama (agent 3).

create table my_projects (
  id         bigserial primary key,
  name       text not null,
  repo_url   text unique not null,
  summary    text not null,
  skills     text[] not null default '{}',
  readme_sha text,
  embedding  vector(1024),
  updated_at timestamptz not null default now()
);

create index on my_projects using hnsw (embedding vector_cosine_ops);

create table drafts (
  id             bigserial primary key,
  opportunity_id bigint references opportunities(id),
  kind           text not null check (kind in ('cover_letter', 'message', 'post', 'analysis')),
  content        text not null,
  created_at     timestamptz not null default now()
);

create index on drafts (opportunity_id, kind, created_at desc);

-- Faza 1 agenta 3 (istraživanje) piše samo ovo; faza 2 (pisanje) čita.
create table company_notes (
  opportunity_id bigint primary key references opportunities(id),
  notes          jsonb not null,
  created_at     timestamptz not null default now()
);

grant select on my_projects, drafts, company_notes to agent_reader;
