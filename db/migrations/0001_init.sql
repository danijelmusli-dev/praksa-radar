create extension if not exists vector;

create table opportunities (
  id          bigserial primary key,
  url         text unique not null,
  source      text not null,
  title       text not null,
  org         text,
  location    text,
  type        text,
  deadline    date,
  raw_text    text,
  summary_sr  text,
  embedding   vector(1024),   -- Voyage voyage-3.5-lite
  first_seen  timestamptz default now()
);

create index on opportunities using hnsw (embedding vector_cosine_ops);

create table scores (
  opportunity_id  bigint primary key references opportunities(id),
  profile_version bigint,
  similarity      real,
  relevance       real,
  reason          text,
  passed          boolean not null default false,
  run_id          text,
  sent_at         timestamptz
);

create table source_runs (
  id          bigserial primary key,
  source      text not null,
  started_at  timestamptz not null default now(),
  items_found integer not null default 0,
  error       text
);

create index on source_runs (source, started_at desc);

create table kv_state (
  key   text primary key,
  value jsonb
);
