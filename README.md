# Praksa Radar

Lični career agent. Svakog dana prikuplja prakse, studentske projekte i radionice, rangira ih prema mom promenljivom profilu i šalje ih na Telegram. Uz to piše gap analizu i nacrte prijava (šaljem ih ja, agent ništa ne šalje u moje ime) i sam prati da li izvori još rade.

> Jedan korisnik, jedan interfejs (Telegram). Sve stanje je u jednoj Postgres bazi; kod je javan, lični podaci nisu.

## Kako radi

```mermaid
flowchart LR
  subgraph Izvori
    G[Gmail labela<br/>LinkedIn alerti, newsletteri]
    H[HelloWorld.rs<br/>career stranice]
    I[Instagram Graph API]
  end

  subgraph "GitHub Actions (cron 2x dnevno)"
    C[collect<br/>adapteri + dedup] --> R[rank<br/>filteri → embedding → LLM rerank]
    R --> S[send<br/>digest]
    T[tasks<br/>analiza, nacrt, sync]
    A[agents<br/>samopopravka, otkrivanje]
  end

  DB[(Supabase Postgres<br/>+ pgvector)]
  TG((Telegram))
  W[Cloudflare Worker<br/>webhook, komande, MCP]
  WD[healthchecks.io]

  G & H & I --> C
  C <--> DB
  R <--> DB
  S --> TG
  TG -- komande, dugmići --> W
  W <--> DB
  W -- repository_dispatch --> T & A
  T & A --> DB
  T & A --> TG
  S -. ping .-> WD
  W -. cron: last_successful_run .-> DB
```

Dve putanje dele istu bazu: zakazani pipeline (puni bazu i šalje digest) i mali webhook koji odmah odgovara na komande. Teške stvari (analiza, nacrt) Worker prosleđuje u Actions, a rezultat stiže istim putem kao digest.

### Filter u pet koraka (od najjeftinijeg ka najskupljem)

1. **Profil**: opisan rečima (`/filter dodaj: embedded i AI, bez čistog frontenda`), LLM ga pretvara u JSON, svaka izmena je nova verzija (`/filter undo`).
2. **Tvrdi filteri**: grad ili remote, tip, rok, isključenja. Bez troška.
3. **Semantički rang**: kosinusna sličnost embeddinga (Voyage), dalje ide prvih 30.
4. **LLM rerank**: ocena 0–10, tip, rok i jedna rečenica „zašto“. Prag za slanje je podešavanje (`/prag`).
5. **Učenje**: poslednjih 20 ocena (👍/👎/„Manje ovakvih“) ulazi u prompt kao primeri.

Ako LLM ili embeddingi nisu dostupni, rang pada na ključne reči, a digest je označen „bez AI ranga“.

### Agenti

Jezgro je determinističan pipeline; agenti rade samo gde koraci ne mogu da se znaju unapred. Svi dele istu petlju (najviše 15 koraka, fiksni budžet tokena, kill switch pre svakog koraka, trag u `agent_runs`).

| Agent | Šta radi | Šta ne sme |
|---|---|---|
| 1. Samopopravka | Kad izvor padne 3x zaredom, menja selektore/adapter i otvara PR | Da menja išta van adaptera, fixture-a i selektora; da merge-uje |
| 2. Otkrivanje | Traži nove izvore, isprobava selektore, predlaže sa dugmićima | Da doda izvor bez mog klika (pa PR) |
| 3. Istraživanje firme + pisanje | Gap analiza i nacrti prijave | Da pošalje bilo šta; faza koja čita web nema profil, faza koja ima profil nema mrežu |
| 4. Telegram asistent | Slobodan tekst nad alatima radara (MCP) | Da menja profil bez mog dugmeta |

## Bezbednost agenata

Opasna kombinacija („smrtonosni trougao“) je pristup ličnim podacima + čitanje nepouzdanog sadržaja + spoljna komunikacija u istom pokretanju. Nijedan agent nema sva tri.

- **Dve izolovane faze** za agenta 3: istraživanje (web, bez profila i baze) i pisanje (profil, bez mreže).
- **Najmanje privilegije**: Postgres uloge `agent_reader` i `agent_tracker` (niko od agenata ne briše); GitHub App samo za ovaj repo; testovi agentovog koda rade u procesu bez tajni.
- **Web zaštite**: sadržaj se čisti (skripte, skriveni tekst, komentari), skraćuje na 20.000 znakova i označava kao nepouzdan; model ne može da otvori izmišljen URL, URL sa upitom, nepoznat host, privatnu adresu ni link na tuđ domen; `robots.txt` se poštuje.
- **Izlaz**: svaki izlaz agenta ide kroz JSON šemu i odbacuje se, ne „popravlja“. Linkovi u porukama dolaze samo iz kolone `url`.
- **Potvrda čoveka**: izmena profila (`pending_changes`), dodavanje izvora i svaki `agent/*` PR čekaju moj klik/merge. CI (`agent-guard`) odbija diff van dozvoljenih putanja.
- **Kill switch**: `/stop` (`kv_state`) i repo varijabla `AGENTS_ENABLED`.
- **Red-team**: 20 otrovanih stranica; prolaz je 0 izvršenih instrukcija (`evals/redteam`).

## Samooporavak i nadzor

| Kvar | Šta sistem radi sam |
|---|---|
| Prolazna greška (5xx, 429, timeout) | Retry sa eksponencijalnim čekanjem i jitterom, poštuje `Retry-After` |
| Izvor pada 3x zaredom | Circuit breaker: preskače 24 h, pa probna runda; agent 1 otvara PR |
| Popravka pogoršala stvar | 2 runde sa 0 stavki posle merge-a → agent otvara revert PR |
| LLM nedostupan | Degradacija na ključne reči, digest označen |
| Runda pukne na pola | Checkpoint po fazama, nova runda nastavlja |
| Duplo slanje | Idempotentnost (`scores.sent_at`, upsert po URL-u), `concurrency` grupa |
| Istekao token (Gmail, Instagram) | Poruka sa koracima za obnovu |
| Ceo sistem stao | Spoljni watchdog (healthchecks.io) + Worker cron ↔ Actions provere |

Uz to: keepalive za GitHub cron, nedeljni šifrovani `pg_dump` (artifact 28 dana) i nedeljni izveštaj zdravlja.

## Komande

| Komanda | Šta radi |
|---|---|
| `/filter`, `/filter dodaj:` / `ukloni:`, `/filter undo`, `/prag N` | Profil i prag |
| `/izvori`, `/izvori traži` | Status izvora; agent 2 traži nove |
| `/sad` | Pokreće rundu odmah |
| `/prijavio <id>`, `/intervju <id> <datum>`, `/tracker [status]` | Tracker prijava, podsetnici |
| `/analiza <id>`, `/nacrt <id> pismo\|poruka` | Gap analiza, nacrt prijave |
| `/projekti sync`, `/post <repo>` | Moji projekti sa GitHub-a, nacrt LinkedIn posta |
| `/agenti`, `/stop`, `/pokreni` | Poslednja pokretanja agenata, kill switch |
| slobodan tekst | Asistent („nađi ML prakse sa rokom ove nedelje“) |

Dugmići ispod svake stavke digesta: 👍 · 👎 · Manje ovakvih · 💾 · Analiza · Nacrt.

## Rezultati

Cilj je meren brojem, ne utiskom. Brojevi se upisuju posle pokretanja evala (vidi `TASKS-MANUAL.md`, faza H).

| Šta | Kako se meri | Cilj | Rezultat |
|---|---|---|---|
| Preciznost digesta | `python -m evals.run` (100 označenih oglasa) | ≥ 80% | _upisati_ |
| Odziv | isto | ≥ 70% | _upisati_ |
| Tačnost tipa / roka | isto | ≥ 90% | _upisati_ |
| Asistent (alat / odgovor) | `npm run eval:assistant` (25 pitanja) | ≥ 90% / ≥ 80% | _upisati_ |
| Samopopravka | `python -m evals.repair_eval` (10 pokvarenih sajtova) | ≥ 70% | _upisati_ |
| Red-team | `python -m evals.redteam.run` (20 stranica) | 0 izvršenih | _upisati_ |
| Rad bez intervencije | `source_runs`, nedeljni izveštaj | 30 dana | _upisati_ |

Eval se pokreće i kao GitHub Action na svaki PR koji menja prompt ili logiku rangiranja; pad preciznosti za više od 5 p.p. obara build.

## Tehnologije

Python 3.12 (httpx, BeautifulSoup, psycopg, pydantic, uv) · GitHub Actions · Cloudflare Workers (TypeScript, Hyperdrive) · Supabase Postgres + pgvector · Claude (Haiku za rerank, Sonnet za agente i nacrte) · Voyage embeddingi · Telegram Bot API · MCP.

## Struktura

```text
radar/                 pipeline, adapteri (sources/), rang, agenti (agents/), zadaci (tasks.py)
worker/                Cloudflare Worker: webhook, komande, tracker, MCP, asistent
db/migrations/         šema (0001–0005)
config/sources.yaml    izvori (novi izvor = nova stavka, bez novog koda)
evals/                 filter, asistent, samopopravka, red-team
tests/                 Python testovi (fixture-i u tests/fixtures/)
.github/workflows/     radar, tasks, agents, weekly, ci, eval*, redteam
TASKS-MANUAL.md        sve što radim ja ručno (nalozi, tajne, testovi sa pravim servisima)
```

## Pokretanje

```bash
uv sync
docker run -d -p 54329:5432 -e POSTGRES_PASSWORD=postgres pgvector/pgvector:pg16
export TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:54329/postgres
uv run pytest                      # Python testovi (integracioni traže Postgres)
cd worker && npm ci && npm test    # Worker testovi
```

Podešavanje naloga, tajni i ručnih testova je u [TASKS-MANUAL.md](TASKS-MANUAL.md).

## Granice

Čita se samo ono što je javno ili što sam sam prosledio: Gmail (samo čitanje), javni sajtovi uz `robots.txt`, Instagram Graph API. Ništa iza logina (LinkedIn, Instagram, Facebook), bez zaobilaženja zaštite od botova, bez automatskog slanja prijava ili objavljivanja u moje ime.
