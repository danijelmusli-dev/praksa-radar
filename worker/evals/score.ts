/** Ocenjivanje odgovora asistenta (čista logika, bez mreže i baze). */

const MUTATING = new Set(["tracker_add", "set_reminder", "update_profile", "run_radar"]);

export interface Case {
  id: number;
  q: string;
  tool: string | null;
  args?: Record<string, string | number>;
  contains?: string[];
  containsAny?: string[];
  notContains?: string[];
}

export interface Call {
  tool: string;
  args: Record<string, unknown>;
}

function argsMatch(want: Record<string, string | number> | undefined, got: Record<string, unknown>) {
  return Object.entries(want ?? {}).every(([k, v]) =>
    typeof v === "number"
      ? Number(got[k]) === v
      : String(got[k] ?? "").toLowerCase().includes(String(v).toLowerCase()),
  );
}

export function toolOk(c: Case, calls: Call[]): boolean {
  if (c.tool === null) return calls.length === 0;
  const hit = calls.some((x) => x.tool === c.tool && argsMatch(c.args, x.args));
  const stray = calls.some((x) => MUTATING.has(x.tool) && x.tool !== c.tool); // nepotrebna izmena
  return hit && !stray;
}

export function answerOk(c: Case, text: string): boolean {
  const t = text.toLowerCase();
  return (
    (c.contains ?? []).every((s) => t.includes(s.toLowerCase())) &&
    (!c.containsAny || c.containsAny.some((s) => t.includes(s.toLowerCase()))) &&
    !(c.notContains ?? []).some((s) => t.includes(s.toLowerCase()))
  );
}
