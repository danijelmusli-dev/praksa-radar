create table profile_versions (
  id         bigserial primary key,
  raw_text   text not null,
  structured jsonb not null,
  embedding  vector(1024) not null,
  created_at timestamptz not null default now()
);

create table feedback (
  id             bigserial primary key,
  opportunity_id bigint not null references opportunities(id),
  signal         text not null check (signal in ('up', 'down', 'less_like_this')),
  created_at     timestamptz not null default now()
);

create index on feedback (created_at desc);
