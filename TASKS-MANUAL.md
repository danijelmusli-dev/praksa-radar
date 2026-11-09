# Ručni koraci (radiš ti) — Praksa Radar

Sve što ne mogu ja da uradim: nalozi, tajne, odobrenja, ručno označavanje, testiranje sa pravim servisima.
Kod pišem ja do kraja; ovo radimo zajedno kad budeš za računarom. Tajne nikad ne šalji u chat, upisuješ ih samo u GitHub Secrets ili lokalni `.env`.

Redosled: A (jednom, na početku) → B (testiranje Faze 0–1) → C…G po fazama.

---

## A. Prvo podešavanje (potrebno pre bilo kakvog testa)

- [ ] Pokreni Docker Desktop (za lokalni test migracija i integracionih testova)
- [ ] Napravi **javni** GitHub repo `praksa-radar`
- [ ] `git remote add origin …` i prvi push (reci mi kad da commitujem skelet)
- [ ] Branch protection na `main`: zahtevaj PR i zeleni `ci` check
- [ ] **Telegram**: @BotFather → `/newbot` → sačuvaj token. Pošalji botu bilo koju poruku, pa otvori `https://api.telegram.org/bot<TOKEN>/getUpdates` i uzmi `chat.id`
- [ ] **Supabase**: novi projekat (free). SQL editor: `create extension if not exists vector;`
  - Connection string: **Supavisor pooler, session mode, port 5432** (direktna veza je samo IPv6, Actions je IPv4)
- [ ] **Gemini (besplatno)**: https://aistudio.google.com → Get API key (bez kartice). Jedan ključ pokriva LLM i embeddinge. Proveri aktuelne limite besplatnog nivoa; free tier može da koristi podatke za treniranje, pa ne stavljaj osetljive stvari u profil
  - Plaćena alternativa: `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY` + `VOYAGE_API_KEY`
- [ ] **GitHub Secrets** (Settings → Secrets → Actions):
  - `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
  - `GEMINI_API_KEY`
  - `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN` (posle koraka ispod)
- [ ] Lokalni `.env` (kopija `.env.example`) sa istim vrednostima, za lokalno testiranje

## A2. Gmail (potrebno za Fazu 1)

- [ ] GCP projekat → uključi **Gmail API**
- [ ] OAuth consent screen → prebaci u **„In production“** (u „Testing“ refresh token ističe posle ~7 dana)
- [ ] OAuth client tipa **Desktop** → uzmi client id i secret
- [ ] Pokreni lokalno: `GMAIL_CLIENT_ID=… GMAIL_CLIENT_SECRET=… uv run python scripts/gmail_auth.py` → kopiraj `GMAIL_REFRESH_TOKEN` u Secrets i `.env`
- [ ] U Gmail-u napravi labelu `radar`
- [ ] Gmail filter: LinkedIn job alerti (`from:jobalerts-noreply@linkedin.com`) + newsletteri firmi/organizacija → labela `radar`
- [ ] Pošalji sebi (ili sačekaj) bar jedan pravi LinkedIn alert; **prosledi mi jedan anonimizovan primer** (HTML mejla) da napišem parser nad pravim formatom

## A3. Instagram / Meta (pokreni odmah, čekanje je dugo; treba tek u Fazi 5)

- [ ] Instagram nalog prebaci u Business/Creator i veži za Facebook stranicu
- [ ] Meta for Developers → nova app → Instagram Graph API
- [ ] Pošalji **app review** zahtev (Hashtag Search + Business Discovery)

---

## B. Test Faze 0 („gotovo kad“)

- [ ] Actions → `hello` → **Run workflow** → zeleno
- [ ] U Supabase tabeli `kv_state` postoji red `hello`
- [ ] Na Telegram stiže „Praksa Radar: zdravo …“
- [ ] Lokalno: `uv run python -m radar.db migrate` radi; `TEST_DATABASE_URL=… uv run pytest` prolazi (sa Docker Postgresom: `docker run -p 5432:5432 -e POSTGRES_PASSWORD=postgres pgvector/pgvector:pg16`)

## C. Faza 1 — Radar MVP

- [ ] Odluči koje **career stranice** da pratim (1–3 firme/organizacije) i pošalji URL-ove; ja proveravam `robots.txt` i uslove pre dodavanja
- [ ] HelloWorld.rs: reci ako ne želiš da se scrapuje, ili potvrdi da ide posle moje provere robots.txt/uslova
- [ ] Napiši svoj prvi `filter.json` (ključne reči, lokacije, tipovi, isključenja) i učitaj: `uv run python -m radar.cli set-filter filter.json`
- [ ] Gmail: `GMAIL_*` tajne su postavljene (A2); ako nisu, `radar` radi samo sa HelloWorld.rs
- [ ] **Test**: Actions → `radar` → Run workflow → digest stiže na Telegram; pokreni opet odmah → **ništa novo** (idempotentnost)
- [ ] Tek kad je ručna runda OK: Settings → Variables → Actions → nova varijabla `RADAR_ENABLED=true` (to uključuje cron 07:00 i 14:00; do tada raspored ne radi, samo ručno pokretanje)
- [ ] Posle promene na letnje vreme (kraj marta): cron u `radar.yml` pomeri sa `0 6,13` na `0 5,12`
- [ ] Gmail: proveri da LinkedIn alerti pravilno izlaze u digestu (parser je napisan nad izmišljenim primerom; pošalji mi pravi mejl pa ga prilagodim)
- [ ] HelloWorld.rs: pregledaj prvi digest. Pretraga sajta je fuzzy (traži i po opisima), a kad nema pogodaka, sajt vraća zamenske oglase koje adapter preskače. Uslove korišćenja sajta pročitaj ti (robots.txt dozvoljava listu oglasa)
- [ ] **Test**: 7 dana zaredom stiže digest bez intervencije; proveri `source_runs` (nema grešaka)

## D. Faza 2 — Lični filter

- [ ] Napiši profil rečima u `profil.txt` (interesovanja, šta isključuješ, gradovi, nivo, tipovi). Ne commituj ga, profil živi u bazi
- [ ] `GEMINI_API_KEY` u lokalnom `.env` i u GitHub Secrets (workflow `radar` ih već koristi)
- [ ] Učitaj profil: `uv run python -m radar.cli set-profile profil.txt` → proveri izlaz JSON-a (`show-profile`); ako je LLM nešto pogrešno razumeo, doteraj tekst i ponovi (svaki poziv je nova verzija)
- [ ] Prvi pravi rang: ručno pokreni `radar` workflow, pa pogledaj digest. Reason u svakoj stavci mora imati smisla
- [ ] Ako AI nije dostupan, digest nosi oznaku „⚠ bez AI ranga“ — proveri da je to retko (Supabase `scores.reason`, Google AI Studio → usage)
- [ ] **Ručno označi 50 oglasa** (relevantno da/ne): `uv run python -m evals.export 50 > evals/dataset.todo.jsonl`, u svakom redu `"relevant": null` zameni sa `true`/`false` (po želji dodaj `"type"` i `"deadline"` ako znaš tačnu vrednost), sačuvaj kao `evals/dataset.jsonl`
- [ ] Napravi `evals/profile.json` (kopiraj `evals/profile.example.json` i prilagodi). **Pazi:** repo je javan, stavi samo opšte stvari koje ti ne smetaju da budu javne
- [ ] Pokreni eval: `uv run python -m evals.run --min-precision 0.7`. Cilj ≥70% preciznosti; ako nije, odlučujemo da li menjamo prompt, prag ili profil
- [ ] Kad si zadovoljan rezultatom: `uv run python -m evals.run --save-baseline` pa commit `evals/baseline.json` (od tada PR koji spusti preciznost za >5 p.p. obara build)
- [ ] Posle par dana proveri da nisi probio dnevni limit besplatnog nivoa u AI Studiju (rerank ide samo za top 30 po rundi)
- [ ] Opciono: `uv run python -m radar.cli feedback <id> down` za stavke koje ti ne odgovaraju, dok Telegram dugmići ne stignu u Fazi 3 (poslednjih 20 ocena ulazi u prompt)

## E. Faza 3 — Interaktivnost (Telegram Worker, tracker, MCP, asistent)

Kod je gotov (`worker/`, migracija `0003`). Ovo je sve što ostaje tebi, redom:

**Baza**
- [ ] Pokreni migracije na Supabase: `uv run python -m radar.db migrate` (primeni `0002` i `0003`). `0003` pravi uloge `agent_reader` i `agent_tracker` i daje ih korisniku koji pokreće migracije. Worker se mora prijavljivati istim korisnikom (isti `DATABASE_URL`) jer u transakciji radi `set local role ...`

**Cloudflare**
- [ ] Cloudflare nalog (free) pa `cd worker && npm ci && npx wrangler login`
- [ ] Hyperdrive: `npx wrangler hyperdrive create praksa-radar --connection-string="<DATABASE_URL>"` → nalepi dobijeni `id` u `worker/wrangler.toml` (`REPLACE_WITH_HYPERDRIVE_ID`). Proveri da je Hyperdrive dostupan na free planu; ako nije, javi mi pa prelazimo na Supabase REST (PostgREST)
- [ ] U `worker/wrangler.toml` upiši `GITHUB_REPO = "tvoj-nalog/praksa-radar"`
- [ ] Tajne, svaka sa `npx wrangler secret put <IME>`:
  - `TELEGRAM_BOT_TOKEN`
  - `TELEGRAM_SECRET_TOKEN` (nasumičan string, npr. `openssl rand -hex 24`; ne sme biti isti kao bot token)
  - `ALLOWED_CHAT_ID` (tvoj `chat.id` iz koraka A)
  - `GEMINI_API_KEY`
  - `GITHUB_TOKEN` (fine-grained PAT samo za ovaj repo: **Contents: Read and write** za `/sad`, **Variables: Read** za rezervni kill switch)
  - `MCP_TOKEN` (nasumičan string; bez njega je `/mcp` isključen)
- [ ] `npx wrangler deploy` → zapiši URL (`https://praksa-radar.<nalog>.workers.dev`)
- [ ] Postavi webhook: `TELEGRAM_BOT_TOKEN=… TELEGRAM_SECRET_TOKEN=… uv run python scripts/set_webhook.py https://praksa-radar.<nalog>.workers.dev/telegram`
- [ ] Proveri `https://…workers.dev/health` → `ok`

**GitHub**
- [ ] Repo varijabla `AGENTS_ENABLED=true` (Settings → Variables). Kad je `false`, svi agenti staju (rezervni prekidač, menja se i sa telefona)

**Testovi (ručni, sa pravim servisima)**
- [ ] `/help`, `/filter` (prikaz profila), `/prag 6` pa `/filter` (prag je 6)
- [ ] `/filter dodaj: embedded i AI, bez čistog frontenda` → stiže predlog sa ✅/❌; ✅ pravi novu verziju; ❌ ne menja ništa
- [ ] `/filter undo` vraća prethodni sadržaj
- [ ] `/izvori` pokazuje status HelloWorld/Gmail; `/sad` pokreće `radar` workflow i digest stiže za ~2 min
- [ ] **Promena filtera iz Telegrama važi u sledećoj rundi** (kriterijum Faze 3): promeni profil, pokreni `/sad`, vidi razliku u digestu
- [ ] Digest ima numerisane stavke i dugmiće (👍 👎 Manje 💾 po stavci): 👍/👎 upisuju u `feedback`; „Manje“ predlaže novo isključenje na potvrdu; 💾 upisuje u tracker
- [ ] `/prijavio <id>`, `/intervju <id> <datum>`, `/tracker`; sutradan proveri da stiže podsetnik (cron radi na sat)
- [ ] Slobodan tekst: „nađi ML prakse sa rokom ove nedelje“ → odgovor sa #id i linkovima iz baze (nikad tuđi linkovi)
- [ ] `/stop` pa pošalji slobodan tekst → „Agenti su isključeni“; `/pokreni` vraća. Isto probaj sa repo varijablom `AGENTS_ENABLED=false`
- [ ] `/agenti` prikazuje poslednja pokretanja
- [ ] **Poruka sa tuđeg naloga** (ili iz grupe) → bot ne odgovara ništa
- [ ] Direktan POST na `/telegram` bez secret zaglavlja → 403: `curl -i -X POST https://…workers.dev/telegram -d '{}'`
- [ ] Opciono, MCP: u Claude Desktop/Code dodaj remote MCP server `https://…workers.dev/mcp` sa zaglavljem `Authorization: Bearer <MCP_TOKEN>` i probaj „koje prakse imam sačuvane?“

**Eval asistenta** (kriterijum Faze 3: „asistent tačno odgovara na eval pitanja“)
- [ ] Pregledaj 25 pitanja u `worker/evals/assistant.cases.json` (očekivani alat, parametri i odgovor); promeni šta ti ne odgovara
- [ ] Pokreni nad pravim modelom (troši dnevnu kvotu besplatnog nivoa): `cd worker && GEMINI_API_KEY=… TEST_DATABASE_URL=postgresql://… npm run eval:assistant`, ili Actions → `eval-assistant` → Run workflow. Cilj: tačnost alata ≥90%, odgovora ≥80%. Rezultat je u `worker/evals/last-run.json`; promašaji se ispisuju
- [ ] Gemini: proveri kvotu posle prvih dana (asistent koristi `gemini-flash-latest`; budžet je 60.000 tokena po pitanju, max 15 koraka)

## F. Faza 4 — Helper (projekti, analiza, nacrti)

Kod je gotov (migracija `0004`, `radar/tasks.py`, `radar/agents/`, workflow `tasks`).

**Podešavanje**
- [ ] `uv run python -m radar.db migrate` na Supabase (primeni `0004`)
- [ ] GitHub Secrets (pored postojećih): `OWNER_TERMS` (zarezom odvojeno: tvoje ime, mejl, grad... Upiti za pretragu koji ih sadrže se odbijaju; može i prazno) i, opciono, `BRAVE_API_KEY` ([Brave Search API](https://brave.com/search/api/), proveri aktuelne kvote). Bez Brave ključa istraživanje čita samo stranicu oglasa i linkove sa nje
- [ ] Worker koristi isti `GITHUB_TOKEN` (PAT iz faze E) za `repository_dispatch` događaj `task`. Proveri da ima **Contents: Read and write** (potreban i za `/sad`); posle promene `cd worker && npx wrangler deploy`
- [ ] Moji javni repoi treba da imaju README; `GITHUB_USER` nije potreban u Actions (koristi vlasnika repoa)

**Testovi (ručni, sa pravim servisima)**
- [ ] `/projekti sync` → za ~1 min poruka „Projekti: N novih…“; u Supabase `my_projects` ima redove sa `summary`, `skills`, `embedding`. Proveri da li su opisi i veštine tačni (nije izmišljeno)
- [ ] Dugme **Analiza** ispod stavke → analiza sa pokrivenošću (npr. 4/6), ✅/❌ po zahtevima, „Fali ti“ i jednim predlogom. Proveri da su činjenice o firmi stvarne
- [ ] Dugme **Nacrt** → izaberi pismo ili poruku → nacrt na jeziku oglasa (pismo ≤250 reči, poruka ≤80). **Od klika do teksta < 2 min** (kriterijum Faze 4); javi ako je sporije pa gledamo gde se gubi vreme
- [ ] Nacrt nikad nije poslat nikome; pročitaj ga, doteraj i pošalji sam
- [ ] `/nacrt <id> pismo`, `/nacrt <id> poruka`, `/analiza <id>` rade i kao komande
- [ ] Posle runde sa stavkama 8+ stiže automatska analiza (najviše 3 po rundi; `AUTO_ANALYSIS_MAX=0` je isključuje)
- [ ] `/stop` pa pokušaj Analiza → „Agenti su isključeni“; `/pokreni` vraća
- [ ] `/agenti` prikazuje pokretanja `agent3-istrazivanje`; proveri korake i tokene; anomalije (>10 koraka, odbijen alat) stižu kao ⚠ poruka
- [ ] Oceni **10 nacrta** ocenom 1–5 po rubrici: tačnost (ništa izmišljeno), konkretnost (pominje prave projekte), ton. Cilj: prosek ≥4. Ako je niže, doteramo prompt u `radar/agents/writer.py`
- [ ] Iz Supabase (`drafts`) možeš da vidiš sve nacrte i analize; `company_notes` su beleške o firmama

## G. Faza 5 — Širenje i nadzor

Kod je gotov (migracija `0005`, `radar/health.py`, `radar/sources/instagram.py`, `radar/agents/{repair,discovery,proposals,guard}.py`, workflow-i `agents`, `weekly`, `redteam`, CI guard). Ovo je sve što ostaje tebi.

**Pre svega**
- [ ] `uv run python -m radar.db migrate` na Supabase (primeni `0005`)
- [ ] `cd worker && npx wrangler deploy` (novo: `/izvori traži`, `/post`, dugmići za predloge izvora, watchdog u cron-u)

**Spoljni watchdog (healthchecks.io)**
- [ ] Nalog na healthchecks.io (proveri aktuelne limite free nivoa) → novi check: period **1 dan**, grace **2 h** (upozori posle ~26 h bez pinga) → kopiraj ping URL
- [ ] GitHub Secret `HEALTHCHECK_URL` = taj URL; poveži check sa Telegramom/mejlom
- [ ] Repo varijabla `WORKER_URL` = `https://praksa-radar.<nalog>.workers.dev` (Actions proverava `/health` svake runde)
- [ ] Kad uključiš cron (`RADAR_ENABLED=true`), u `worker/wrangler.toml` postavi `WATCHDOG = "on"` pa `npx wrangler deploy` (Worker javlja ako nema uspešne runde 26 h)
- [ ] **Test**: ugasi workflow `radar` u Actions (Disable workflow) na ~27 h → stižu OBA upozorenja (healthchecks.io i Worker). Vrati ga

**Instagram (Graph API)** — preskoči ako app review nije prošao; radar radi i bez njega
- [ ] Meta app review odobren (Hashtag Search + Business Discovery, A3)
- [ ] Secrets: `INSTAGRAM_ACCESS_TOKEN` (long-lived, važi ~60 dana: stavi podsetnik na 50 dana; kad istekne stiže ⚠ poruka) i `INSTAGRAM_USER_ID` (ID Business naloga)
- [ ] U `config/sources.yaml` odkomentariši/dodaj blok `instagram` (hashtagovi i nalozi organizacija koje pratiš). **Najviše 30 različitih hashtagova u 7 dana**; `recent_media` vraća samo poslednja 24 h
- [ ] Posle prve runde proveri `/izvori`; ako je Instagram uvek prazan, proveri tokene i dozvole

**GitHub App za agente 1 i 2 (PR-ovi)**
- [ ] GitHub → Settings → Developer settings → GitHub Apps → New: dozvole samo **Contents: Read & write** i **Pull requests: Read & write**; instaliraj **samo na ovaj repo**
- [ ] Generiši private key (.pem). Secrets: `GH_APP_ID`, `GH_APP_INSTALLATION_ID` (broj iz URL-a instalacije), `GH_APP_PRIVATE_KEY` (ceo PEM)
- [ ] Branch protection na `main`: obavezan PR i zeleni `ci` (uključujući `agent-guard`); **merge svih `agent/*` PR-ova radiš ti**
- [ ] Settings → Actions → General → proveri da workflow-i imaju samo read dozvole po defaultu (App piše, ne GITHUB_TOKEN)

**Otkrivanje izvora (agent 2)**
- [ ] `BRAVE_API_KEY` Secret (proveri aktuelne kvote); bez njega agent 2 ne radi
- [ ] `/izvori traži` → u narednih par minuta stižu predlozi sa dugmićima ✅ Dodaj / ❌ Odbaci. Pre ✅ **pročitaj uslove korišćenja sajta**; ✅ otvara PR koji ti mergeuješ
- [ ] Opciono: teme za pretragu u `config/sources.yaml` pod `discovery: {topics: [...]}`

**Nedeljni poslovi (`weekly` workflow, nedeljom 20:00)**
- [ ] Secret `DUMP_PASSPHRASE` (jaka lozinka; **sačuvaj je u menadžeru lozinki**, bez nje se dump ne može otvoriti). Repo je javan, pa se dump šifruje pre otpremanja
- [ ] Variable `PG_VERSION` ako Supabase baza nije PG 17 (`pg_dump` mora biti iste ili novije verzije)
- [ ] **Test oporavka** (jednom): preuzmi artifact `db-dump`, `gpg -d praksa-radar-dump.sql.gz.gpg | gunzip | psql <prazna baza>` i proveri da se podaci vrate
- [ ] Prva nedelja: stiže izveštaj zdravlja (izvori, runde, agenti) i uvid u tržište na Telegram
- [ ] Keepalive: workflow ponovo uključuje cron-ove jednom nedeljno. **Proveri posle ~60 dana** da cron i dalje radi (GitHub-ova pravila se menjaju; ako se ugasi, uključi ga ručno u Actions)

**Testovi (ručni, sa pravim servisima)**
- [ ] **Circuit breaker**: u `config/sources.yaml` namerno pokvari selektor jednog `html_generic` izvora (ili ugasi mrežu) → posle 3 neuspele runde stiže ⛔ poruka, izvor se preskače 24 h, a agent 1 otvara PR (`agent/repair-…`). Proveri diff, mergeuj, pa vidi da izvor ponovo radi
- [ ] **Revert**: namerno mergeuj lošu popravku → posle 2 runde sa 0 stavki agent otvara `agent/revert-…` PR (tražiće tvoje odobrenje)
- [ ] **CI guard**: ručno otvori PR sa grane `agent/repair-test` koji menja `pyproject.toml` → `agent-guard` mora da pada
- [ ] `/stop` odmah zaustavlja agente (i u toku rada: proverava se pre svakog koraka)
- [ ] **Red-team**: Actions → `redteam` → Run workflow (pravi model, nekoliko centi). Mora biti **0 izvršenih instrukcija**; svaki promašaj obara build. Isto lokalno: `uv run python -m evals.redteam.run`
- [ ] **Eval agenta 1**: `uv run python -m evals.repair_eval` (10 pokvarenih sajtova). Cilj ≥70% popravki koje prolaze; zapiši rezultat
- [ ] **Eval agenta 2** (nije automatizovan): sakrij 20 poznatih dobrih izvora iz `sources.yaml`, pokreni `/izvori traži` 4 nedelje i izbroj koliko je pronađeno; prati udeo predloga koje prihvataš
- [ ] **Eval agenta 3** (nije automatizovan): napiši „idealnu“ gap analizu za 10 oglasa i uporedi ocenom 1–5 + proveri činjenice o firmi
- [ ] Gemini: proveri dnevne limite (agenti troše tokene; `/agenti` i nedeljni izveštaj pokazuju korake i tokene)
- [ ] **30 dana rada bez ručne intervencije** (kriterijum uspeha Faze 5)

## H. Faza 6 — Dorada za CV

README sa dijagramom arhitekture je napisan ([README.md](README.md)). Ostalo je tvoje:

- [ ] Proširi eval skup na **100** označenih oglasa (`python -m evals.export 100`), cilj preciznost ≥80%, pa upiši brojeve u tabelu „Rezultati“ u README-u (`python -m evals.run`)
- [ ] Snimi **demo GIF** iz Telegrama (digest → 👍 → Analiza → Nacrt) i dodaj ga u README (`docs/demo.gif`)
- [ ] U README-u zameni `<korisnik>` svojim GitHub nalogom i dodaj link na Telegram bota (opciono)
- [ ] `/post praksa-radar` → pročitaj nacrt, doradi i **objavi ručno** na LinkedIn-u
- [ ] Završni prolaz: sve kvačice iz ovog fajla, pa zapiši na CV jednu rečenicu: „Preciznost digesta X% na eval skupu od 100 oglasa; 30 dana rada bez intervencije“

---

## Otvorena pitanja (odluke tvoje)

- [ ] Ritam digesta: ostaje 2x dnevno ili češće za kratke rokove?
- [ ] Jezik nacrta: uvek jezik oglasa ili po komandi?
- [ ] Konačno ime projekta (radno: `praksa-radar`)
