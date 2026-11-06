import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { hasValidSecret, isAllowedChat, safeEqual } from "../src/auth";
import type { Sql } from "../src/env";
import { normalizeProfile, diffProfiles } from "../src/profile";
import { handleUpdate } from "../src/index";
import { stripLinks } from "../src/telegram";
import { Net, PROFILE, connect, hasDb, makeCtx, resetDb, say, update } from "./helpers";

describe("auth", () => {
  const env = makeCtx(undefined as never, new Net()).env;

  it("safeEqual", () => {
    expect(safeEqual("abc", "abc")).toBe(true);
    expect(safeEqual("abc", "abd")).toBe(false);
    expect(safeEqual("abc", "abcd")).toBe(false);
  });

  it("webhook traži tačan secret_token", () => {
    const req = (h: Record<string, string>) => new Request("https://w/telegram", { method: "POST", headers: h });
    expect(hasValidSecret(req({ "X-Telegram-Bot-Api-Secret-Token": "sekret" }), env)).toBe(true);
    expect(hasValidSecret(req({ "X-Telegram-Bot-Api-Secret-Token": "pogresan" }), env)).toBe(false);
    expect(hasValidSecret(req({}), env)).toBe(false);
    expect(hasValidSecret(req({ "X-Telegram-Bot-Api-Secret-Token": "" }), { ...env, TELEGRAM_SECRET_TOKEN: "" })).toBe(false);
  });

  it("samo moj chat_id (poruke i dugmići)", () => {
    const msg = (chat: number, from = chat) => ({ update_id: 1, message: { chat: { id: chat }, from: { id: from }, text: "x" } });
    expect(isAllowedChat(msg(42), env)).toBe(true);
    expect(isAllowedChat(msg(43), env)).toBe(false);
    expect(isAllowedChat(msg(42, 99), env)).toBe(false); // grupa u kojoj piše neko drugi
    expect(isAllowedChat({ update_id: 1, callback_query: { id: "c", from: { id: 99 }, message: { chat: { id: 42 }, message_id: 1 } } }, env)).toBe(false);
    expect(isAllowedChat({ update_id: 1 }, env)).toBe(false);
    expect(isAllowedChat(msg(42), { ...env, ALLOWED_CHAT_ID: "" })).toBe(false);
  });
});

describe("profil i tekst", () => {
  it("normalizeProfile je strog, a prazna polja popunjava", () => {
    expect(normalizeProfile({}).tipovi).toContain("praksa");
    expect(normalizeProfile({ tipovi: [] }).tipovi).toEqual([]);
    expect(normalizeProfile({ interesovanja: [" a ", "a", ""] }).interesovanja).toEqual(["a"]);
    expect(() => normalizeProfile({ interesovanja: "x" })).toThrow();
    expect(() => normalizeProfile({ prag_relevantnosti: 11 })).toThrow();
    expect(() => normalizeProfile([])).toThrow();
  });

  it("diffProfiles", () => {
    const b = { ...PROFILE, interesovanja: ["python", "embedded"], prag_relevantnosti: 6 };
    expect(diffProfiles(PROFILE, b)).toEqual(["+ interesovanja: embedded", "− interesovanja: ML", "prag: 7 → 6"]);
    expect(diffProfiles(PROFILE, PROFILE)).toEqual([]);
  });

  it("stripLinks uklanja http, https i www", () => {
    expect(stripLinks("vidi https://a.b/c?d=1 i www.zlo.com, ok")).toBe("vidi i ok");
    expect(stripLinks("bez linkova")).toBe("bez linkova");
  });
});

describe.skipIf(!hasDb)("handleUpdate", () => {
  let sql: Sql;
  let net: Net;
  beforeAll(() => {
    sql = connect();
  });
  afterAll(async () => sql.end());
  beforeEach(async () => {
    await resetDb(sql);
    net = new Net();
  });

  it("tuđ chat se tiho ignoriše: nema odgovora, nema obrade", async () => {
    const stranger = { update_id: 1, message: { chat: { id: 999 }, from: { id: 999 }, text: "/stop" } };
    expect(await handleUpdate(makeCtx(sql, net), stranger)).toBe(false);
    expect(net.calls).toHaveLength(0);
    expect((await sql`select 1 from kv_state where key = 'agents_enabled'`).length).toBe(0);
  });

  it("isti update_id se obrađuje samo jednom (Telegram ponavlja isporuku)", async () => {
    expect(await handleUpdate(makeCtx(sql, net), update("/help", 10))).toBe(true);
    expect(await handleUpdate(makeCtx(sql, net), update("/help", 10))).toBe(false);
    expect(await handleUpdate(makeCtx(sql, net), update("/help", 9))).toBe(false);
    expect(net.sent).toHaveLength(1);
    expect(await handleUpdate(makeCtx(sql, net), update("/help", 11))).toBe(true);
  });

  it("tekst bez / ide asistentu, komanda ne zove model", async () => {
    net.claude.push(say("Zdravo!"));
    await handleUpdate(makeCtx(sql, net), update("ćao", 1));
    expect(net.lastText).toBe("Zdravo!");
    await handleUpdate(makeCtx(sql, net), update("/help", 2));
    expect(net.claudeCalls).toHaveLength(1);
  });

  it("greška u obradi daje korisniku poruku, ne ruši", async () => {
    // nema profila ni odgovora modela u redu: /filter dodaj: baca, update se ipak potvrđuje
    await handleUpdate(makeCtx(sql, net), update("/filter dodaj: x", 1));
    expect(net.lastText).toContain("Došlo je do greške");
  });
});
