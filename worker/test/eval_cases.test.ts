import { describe, expect, it } from "vitest";
import { TOOLS } from "../src/tools";
import { answerOk, toolOk } from "../evals/score";
import cases from "../evals/assistant.cases.json";
import { OPPS } from "../evals/seed";

describe("eval skup asistenta", () => {
  it("ima 25 pitanja sa jedinstvenim id-jem i postojećim alatima", () => {
    expect(cases).toHaveLength(25);
    expect(new Set(cases.map((c) => c.id)).size).toBe(25);
    const names = new Set(TOOLS.map((t) => t.spec.name));
    for (const c of cases) if (c.tool !== null) expect(names.has(c.tool), `alat ${c.tool}`).toBe(true);
  });

  it("pokriva svih 8 alata i jedno pitanje bez alata", () => {
    const used = new Set(cases.map((c) => c.tool));
    for (const t of TOOLS) expect(used.has(t.spec.name), t.spec.name).toBe(true);
    expect(used.has(null)).toBe(true);
  });

  it("očekivani #id-jevi postoje u seed podacima", () => {
    const refs = cases.flatMap((c) => [...(c.contains ?? []), ...(c.notContains ?? [])]);
    for (const m of refs.filter((s) => s.startsWith("#"))) {
      expect(Number(m.slice(1)), m).toBeLessThanOrEqual(OPPS.length);
    }
  });

  it("ocenjivanje: tačan alat, pogrešni parametri, nepotrebna izmena, odgovor", () => {
    const c = { id: 1, q: "", tool: "tracker_add", args: { id: 3, status: "prijavljen" } };
    expect(toolOk(c, [{ tool: "tracker_add", args: { id: 3, status: "Prijavljen" } }])).toBe(true);
    expect(toolOk(c, [{ tool: "tracker_add", args: { id: 4, status: "prijavljen" } }])).toBe(false);
    const read = { id: 2, q: "", tool: "search_opportunities" };
    expect(toolOk(read, [{ tool: "search_opportunities", args: {} }, { tool: "run_radar", args: {} }])).toBe(false);
    expect(toolOk({ id: 3, q: "", tool: null }, [])).toBe(true);
    expect(toolOk({ id: 3, q: "", tool: null }, [{ tool: "get_profile", args: {} }])).toBe(false);

    const a = { id: 4, q: "", tool: null, contains: ["#1"], containsAny: ["ne mogu", "nemam"], notContains: ["#9"] };
    expect(answerOk(a, "Ne mogu, ali #1 je tu")).toBe(true);
    expect(answerOk(a, "Ne mogu, #1 i #9")).toBe(false);
    expect(answerOk(a, "Evo #1")).toBe(false);
  });
});
