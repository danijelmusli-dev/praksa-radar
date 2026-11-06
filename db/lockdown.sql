-- Zaključavanje baze. Izvršava se na kraju SVAKOG `python -m radar.db migrate` (idempotentno).
--
-- Supabase izlaže tabele iz šeme `public` preko javnog REST API-ja (anon/authenticated + publishable
-- ključ). Ovaj projekat NE koristi taj API: sve ide preko direktne Postgres veze (Python, Worker).
-- Zato: uključi RLS na svakoj tabeli (nova tabela je zaključana automatski), oduzmi prava javnim
-- ulogama i daj agent ulogama tačno ono što im treba.

do $$
declare
  t text;
  r text;
begin
  foreach r in array array['anon', 'authenticated'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('revoke all on all tables in schema public from %I', r);
      execute format('revoke all on all sequences in schema public from %I', r);
      execute format('revoke all on all functions in schema public from %I', r);
      -- buduće tabele iste ere (napravljene ovim korisnikom) ne nasleđuju prava
      execute format('alter default privileges in schema public revoke all on tables from %I', r);
      execute format('alter default privileges in schema public revoke all on sequences from %I', r);
      execute format('alter default privileges in schema public revoke all on functions from %I', r);
    end if;
  end loop;

  -- RLS na svim tabelama; vlasnik (korisnik koji se prijavljuje) ga zaobilazi, agent uloge ne.
  for t in select tablename from pg_tables where schemaname = 'public' loop
    execute format('alter table public.%I enable row level security', t);
  end loop;

  -- agent_reader: samo čitanje tabela na koje ima grant (vidi migracije 0003-0005).
  foreach t in array array[
    'opportunities', 'scores', 'profile_versions', 'applications', 'reminders', 'feedback',
    'pending_changes', 'source_runs', 'agent_runs', 'my_projects', 'drafts', 'company_notes',
    'source_proposals'
  ] loop
    if to_regclass('public.' || t) is not null then
      execute format('drop policy if exists agent_reader_select on public.%I', t);
      execute format('create policy agent_reader_select on public.%I for select to agent_reader using (true)', t);
    end if;
  end loop;

  -- agent_tracker: čita oglase, a piše samo u applications i reminders.
  for t in select unnest(array['opportunities', 'applications', 'reminders']) loop
    execute format('drop policy if exists agent_tracker_select on public.%I', t);
    execute format('create policy agent_tracker_select on public.%I for select to agent_tracker using (true)', t);
  end loop;
  for t in select unnest(array['applications', 'reminders']) loop
    execute format('drop policy if exists agent_tracker_insert on public.%I', t);
    execute format('create policy agent_tracker_insert on public.%I for insert to agent_tracker with check (true)', t);
    execute format('drop policy if exists agent_tracker_update on public.%I', t);
    execute format('create policy agent_tracker_update on public.%I for update to agent_tracker using (true) with check (true)', t);
  end loop;
end $$;
