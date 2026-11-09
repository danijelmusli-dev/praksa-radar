/**
 * Eval agenta 4 (Telegram asistent) nad pravim modelom: 25 pitanja sa očekivanim alatom,
 * parametrima i odgovorom. Mere se tačnost izbora alata, tačnost odgovora i trošak (tokeni).
 *
 *   GEMINI_API_KEY=... (ili ANTHROPIC_API_KEY=...) TEST_DATABASE_URL=... npm run eval:assistant
 *
 * Telegram i GitHub su lažni; baza je prava (seed.ts). Prag: TOOL_MIN (0.9), ANSWER_MIN (0.8).
 */
import { writeFileSync } from "node:fs";
import { afterAll, describe, expect, it } from "vitest";
import { runAssistant } from "../src/agent";
import { Net, connect, hasDb, makeCtx, resetDb } from "../test/helpers";
import cases from "./assistant.cases.json";
import { type Call, type Case, answerOk, toolOk } from "./score";
import { resetState, seed } from "./seed";

const GEMINI = process.env.GEMINI_API_KEY;
const ANTHROPIC = process.env.ANTHROPIC_API_KEY;
describe.skipIf(!(hasDb && (GEMINI || ANTHROPIC)))("eval asistenta (pravi model)", () => {
  const sql = hasDb ? connect() : (undefined as never);
  afterAll(async () => sql?.end());

  it("25 pitanja", { timeout: 900_000 }, async () => {
    await resetDb(sql);
    await seed(sql);
    const rows: Record<string, unknown>[] = [];

    for (const c of cases as Case[]) {
      await resetState(sql);
      const net = new Net();
      const ctx = makeCtx(sql, net, { GEMINI_API_KEY: GEMINI, ANTHROPIC_API_KEY: ANTHROPIC });
      // Samo LLM API ide na pravu mrežu; Telegram i GitHub ostaju lažni.
      ctx.fetch = ((i: RequestInfo | URL, o?: RequestInit) =>
        /^https:\/\/(api\.anthropic\.com|generativelanguage\.googleapis\.com)/.test(String(i))
          ? fetch(i, o)
          : net.fetch(i, o)) as typeof fetch;

      const res = await runAssistant({ ...ctx, chatId: "42", seen: new Set() }, c.q);
      const [run] = await sql`select trace, input_tokens, output_tokens from agent_runs order by id desc limit 1`;
      const calls: Call[] = (run!.trace as Array<{ tool?: string; args?: string }>)
        .filter((t) => t.tool)
        .map((t) => ({ tool: t.tool!, args: safeJson(t.args) }));

      rows.push({
        id: c.id,
        q: c.q,
        tools: calls.map((x) => x.tool).join(",") || "—",
        tool_ok: toolOk(c, calls),
        answer_ok: answerOk(c, res.text),
        steps: res.steps,
        tokens: Number(run!.input_tokens) + Number(run!.output_tokens),
        outcome: res.outcome,
        answer: res.text.slice(0, 200),
      });
    }

    const n = rows.length;
    const pct = (k: string) => rows.filter((r) => r[k]).length / n;
    const summary = {
      n,
      tool_accuracy: pct("tool_ok"),
      answer_accuracy: pct("answer_ok"),
      avg_steps: rows.reduce((s, r) => s + (r.steps as number), 0) / n,
      avg_tokens: Math.round(rows.reduce((s, r) => s + (r.tokens as number), 0) / n),
    };
    console.table(rows.map(({ answer: _a, ...r }) => r));
    console.log(summary);
    for (const r of rows.filter((r) => !r.tool_ok || !r.answer_ok)) console.log("PROMAŠAJ", r);
    writeFileSync(new URL("./last-run.json", import.meta.url), JSON.stringify({ summary, rows }, null, 2));

    expect(summary.tool_accuracy).toBeGreaterThanOrEqual(Number(process.env.TOOL_MIN ?? 0.9));
    expect(summary.answer_accuracy).toBeGreaterThanOrEqual(Number(process.env.ANSWER_MIN ?? 0.8));
  });
});

function safeJson(s: string | undefined): Record<string, unknown> {
  try {
    return s ? (JSON.parse(s) as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}
