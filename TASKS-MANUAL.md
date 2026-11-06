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
- [ ] **Anthropic**: API ključ + postavi mesečni limit potrošnje u konzoli
- [ ] **Voyage AI**: API ključ
- [ ] **GitHub Secrets** (Settings → Secrets → Actions):
  - `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
  - `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`
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
- [ ] `ANTHROPIC_API_KEY` i `VOYAGE_API_KEY` u lokalnom `.env` i u GitHub Secrets (workflow `radar` ih već koristi)
- [ ] Učitaj profil: `uv run python -m radar.cli set-profile profil.txt` → proveri izlaz JSON-a (`show-profile`); ako je LLM nešto pogrešno razumeo, doteraj tekst i ponovi (svaki poziv je nova verzija)
- [ ] Prvi pravi rang: ručno pokreni `radar` workflow, pa pogledaj digest. Reason u svakoj stavci mora imati smisla
- [ ] Ako AI nije dostupan, digest nosi oznaku „⚠ bez AI ranga“ — proveri da je to retko (Supabase `scores.reason`, Anthropic konzola)
- [ ] **Ručno označi 50 oglasa** (relevantno da/ne): `uv run python -m evals.export 50 > evals/dataset.todo.jsonl`, u svakom redu `"relevant": null` zameni sa `true`/`false` (po želji dodaj `"type"` i `"deadline"` ako znaš tačnu vrednost), sačuvaj kao `evals/dataset.jsonl`
- [ ] Napravi `evals/profile.json` (kopiraj `evals/profile.example.json` i prilagodi). **Pazi:** repo je javan, stavi samo opšte stvari koje ti ne smetaju da budu javne
- [ ] Pokreni eval: `uv run python -m evals.run --min-precision 0.7`. Cilj ≥70% preciznosti; ako nije, odlučujemo da li menjamo prompt, prag ili profil
- [ ] Kad si zadovoljan rezultatom: `uv run python -m evals.run --save-baseline` pa commit `evals/baseline.json` (od tada PR koji spusti preciznost za >5 p.p. obara build)
- [ ] Posle par dana proveri potrošnju u Anthropic i Voyage konzoli (rerank ide samo za top 30 po rundi)
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
  - `ANTHROPIC_API_KEY`
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
- [ ] Pokreni nad pravim modelom (košta nekoliko centi): `cd worker && ANTHROPIC_API_KEY=… TEST_DATABASE_URL=postgresql://… npm run eval:assistant`, ili Actions → `eval-assistant` → Run workflow. Cilj: tačnost alata ≥90%, odgovora ≥80%. Rezultat je u `worker/evals/last-run.json`; promašaji se ispisuju
- [ ] Anthropic: proveri potrošnju posle prvih dana (asistent koristi Sonnet; budžet je 60.000 tokena po pitanju, max 15 koraka)

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

- [ ] Meta app review odobren → Instagram token(i), business ID
- [ ] **Brave Search API** ključ (ili Google PSE) za agenta 2; proveri aktuelne kvote
- [ ] **healthchecks.io** nalog + check (period 26 h) → `HEALTHCHECK_URL` Secret; proveri free limite
- [ ] **GitHub App** za agenta 1: dozvole samo contents + PR u ovom repou → app id, private key kao Secrets
- [ ] Branch protection: zahtevaj PR review/check; **tvoj merge** za sve `agent/*` PR-ove
- [ ] Anthropic: mesečni limit potrošnje kod provajdera (ako već nije)
- [ ] **Test**: namerno pokvari selektor jednog izvora → posle 3 runde stiže upozorenje + agent otvara PR
- [ ] **Test**: ugasi Actions cron → watchdog javlja posle 26 h
- [ ] **Test**: `/stop` odmah zaustavlja agente
- [ ] 30 dana rada bez intervencije (kriterijum uspeha)

## H. Faza 6 — Dorada za CV

- [ ] Proširi eval skup na **100** označenih oglasa; cilj ≥80% preciznosti
- [ ] Snimi **demo GIF** iz Telegrama
- [ ] Pregledaj README (dijagram, rezultati evala)
- [ ] `/post <repo>` → pročitaj, doradi i **objavi ručno** na LinkedIn-u

---

## Otvorena pitanja (odluke tvoje)

- [ ] Ritam digesta: ostaje 2x dnevno ili češće za kratke rokove?
- [ ] Jezik nacrta: uvek jezik oglasa ili po komandi?
- [ ] Konačno ime projekta (radno: `praksa-radar`)
