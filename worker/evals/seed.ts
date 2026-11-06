/** Fiksni podaci za eval asistenta. "Danas" je 2026-11-06 (vidi makeCtx u test/helpers.ts). */
import type { Sql } from "../src/env";
import { PROFILE, seedProfile } from "../test/helpers";

interface SeedOpp {
  title: string;
  org: string;
  location: string;
  type: string;
  deadline: string | null;
  text: string;
  score?: [number, string];
}

// Redosled određuje id (1..12) na svežoj bazi; pitanja se pozivaju na te id-jeve.
export const OPPS: SeedOpp[] = [
  { title: "ML Intern", org: "Firma X", location: "Beograd", type: "praksa", deadline: "2026-11-20", text: "Python i osnove ML-a za studente završnih godina", score: [8, "Traže Python i osnove ML-a, Beograd"] },
  { title: "Embedded Weekend", org: "Organizacija Y", location: "Novi Sad", type: "radionica", deadline: "2026-11-22", text: "Arduino i STM32, besplatno za studente", score: [7, "Embedded radionica za studente"] },
  { title: "Backend praksa Python", org: "Firma V", location: "remote", type: "praksa", deadline: "2026-12-01", text: "Django i REST API, letnja praksa", score: [8, "Python backend, remote"] },
  { title: "Frontend Intern React", org: "Firma W", location: "Beograd", type: "praksa", deadline: "2026-11-30", text: "Čist frontend, React i CSS" },
  { title: "Hackathon FTN 2026", org: "FTN", location: "Novi Sad", type: "takmičenje", deadline: "2026-11-15", text: "Dvodnevni hackathon za studente" },
  { title: "Data Science praksa", org: "Firma Z", location: "Beograd", type: "praksa", deadline: "2026-11-10", text: "Pandas, scikit-learn, statistika" },
  { title: "Senior Java Developer", org: "Firma J", location: "Beograd", type: "posao", deadline: null, text: "Minimum 8 godina iskustva" },
  { title: "Cloud DevOps praksa", org: "Firma Q", location: "Beograd", type: "praksa", deadline: "2026-11-25", text: "AWS, docker, CI/CD", score: [6, "Cloud tema, delimično u interesovanjima"] },
  { title: "Letnja praksa Embedded C", org: "Firma E", location: "Niš", type: "praksa", deadline: null, text: "Embedded C na mikrokontrolerima" },
  { title: "AI radionica za studente", org: "Organizacija Z", location: "Beograd", type: "radionica", deadline: "2026-11-12", text: "Uvod u AI i ML alate" },
  { title: "Game dev projekat", org: "Studio G", location: "remote", type: "projekat", deadline: "2026-12-15", text: "Studentski projekat u Unity-ju" },
  { title: "QA Intern", org: "Firma T", location: "Beograd", type: "praksa", deadline: "2026-11-18", text: "Ručno i automatsko testiranje" },
];

export async function seedApplications(sql: Sql): Promise<void> {
  await sql`insert into applications (opportunity_id, status, applied_at) values (1, 'prijavljen', '2026-11-01T10:00:00Z')`;
  await sql`insert into applications (opportunity_id, status) values (3, 'sačuvano')`;
  await sql`insert into applications (opportunity_id, status, next_action_at)
            values (6, 'intervju', '2026-11-12T10:00:00Z')`;
}

export async function seed(sql: Sql): Promise<void> {
  await seedProfile(sql, PROFILE);
  for (const [i, o] of OPPS.entries()) {
    const [row] = await sql`
      insert into opportunities (url, source, title, org, location, type, deadline, raw_text)
      values (${`https://x.rs/${i + 1}`}, 'eval', ${o.title}, ${o.org}, ${o.location}, ${o.type},
              ${o.deadline}, ${o.text}) returning id::int as id`;
    if (o.score) {
      await sql`insert into scores (opportunity_id, relevance, reason, passed)
                values (${row!.id as number}, ${o.score[0]}, ${o.score[1]}, true)`;
    }
  }
  await seedApplications(sql);
}

/** Vrati promenljivo stanje u početno između pitanja (alati mogu da pišu). */
export async function resetState(sql: Sql): Promise<void> {
  await sql`delete from reminders`;
  await sql`delete from pending_changes`;
  await sql`delete from agent_runs`;
  await sql`delete from feedback`;
  await sql`delete from applications`;
  await sql`delete from kv_state`;
  await sql`delete from profile_versions where id > 1`;
  await seedApplications(sql);
}
