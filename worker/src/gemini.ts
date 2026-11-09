/** Gemini API (besplatan nivo): prevodi Anthropic-oblik poruka u generateContent i nazad. */
import type { ClaudeResponse, Msg, ToolSpec } from "./anthropic";
import { ClaudeError } from "./anthropic";
import type { Ctx } from "./env";

const BASE = "https://generativelanguage.googleapis.com/v1beta";

interface Part {
  text?: string;
  thought?: boolean;
  thoughtSignature?: string;
  functionCall?: { name: string; args?: Record<string, unknown> };
  functionResponse?: { name: string; response: Record<string, unknown> };
}

export function toContents(messages: Msg[]): Array<{ role: string; parts: Part[] }> {
  const names = new Map<string, string>(); // tool_use id -> ime alata
  const out: Array<{ role: string; parts: Part[] }> = [];
  for (const m of messages) {
    const role = m.role === "assistant" ? "model" : "user";
    if (typeof m.content === "string") {
      out.push({ role, parts: [{ text: m.content }] });
      continue;
    }
    const parts: Part[] = [];
    for (const b of m.content) {
      if (b.type === "text") {
        if (b.text) parts.push({ text: b.text });
      } else if (b.type === "tool_use") {
        names.set(b.id, b.name);
        parts.push({
          functionCall: { name: b.name, args: b.input ?? {} },
          ...(b.thoughtSignature ? { thoughtSignature: b.thoughtSignature } : {}),
        });
      } else {
        parts.push({
          functionResponse: {
            name: names.get(b.tool_use_id) ?? "alat",
            response: { [b.is_error ? "error" : "output"]: b.content },
          },
        });
      }
    }
    if (parts.length) out.push({ role, parts });
  }
  return out;
}

export async function callGemini(
  ctx: Ctx,
  opts: { model: string; system: string; messages: Msg[]; tools?: ToolSpec[]; maxTokens?: number },
): Promise<ClaudeResponse> {
  const model = opts.model.replace(/^models\//, "");
  const body = {
    contents: toContents(opts.messages),
    systemInstruction: { parts: [{ text: opts.system }] },
    // "razmišljanje" se računa u izlazne tokene, pa mu dajemo prostora
    generationConfig: { maxOutputTokens: (opts.maxTokens ?? 1024) * 2 + 1024 },
    ...(opts.tools?.length
      ? {
          tools: [
            {
              functionDeclarations: opts.tools.map((t) => ({
                name: t.name,
                description: t.description,
                parametersJsonSchema: t.input_schema,
              })),
            },
          ],
        }
      : {}),
  };
  // besplatan nivo ima mali RPM: jedan ponovni pokušaj na 429/5xx
  let res: Response | undefined;
  for (let attempt = 0; attempt < 2; attempt++) {
    res = await ctx.fetch(`${BASE}/models/${model}:generateContent`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-goog-api-key": ctx.env.GEMINI_API_KEY ?? "" },
      body: JSON.stringify(body),
    });
    if (res.ok || (res.status !== 429 && res.status < 500)) break;
    if (attempt === 0) await new Promise((r) => setTimeout(r, 3000));
  }
  if (!res || !res.ok) throw new ClaudeError(`Gemini API ${res?.status ?? "?"}`);

  const data = (await res.json()) as {
    candidates?: Array<{ content?: { parts?: Part[] }; finishReason?: string }>;
    usageMetadata?: { promptTokenCount?: number; candidatesTokenCount?: number; thoughtsTokenCount?: number };
    promptFeedback?: { blockReason?: string };
  };
  const cand = data.candidates?.[0];
  if (!cand) throw new ClaudeError(`Gemini bez odgovora: ${data.promptFeedback?.blockReason ?? "prazan"}`);

  const content: ClaudeResponse["content"] = [];
  let n = 0;
  for (const p of cand.content?.parts ?? []) {
    if (p.functionCall) {
      content.push({
        type: "tool_use",
        id: `gemini_${++n}_${Math.random().toString(36).slice(2, 8)}`,
        name: p.functionCall.name,
        input: p.functionCall.args ?? {},
        ...(p.thoughtSignature ? { thoughtSignature: p.thoughtSignature } : {}),
      });
    } else if (p.text && !p.thought) {
      content.push({ type: "text", text: p.text });
    }
  }
  if (!content.length && cand.finishReason && !["STOP", "MAX_TOKENS"].includes(cand.finishReason)) {
    throw new ClaudeError(`Gemini prekid: ${cand.finishReason}`);
  }
  const u = data.usageMetadata ?? {};
  return {
    content,
    stop_reason: content.some((b) => b.type === "tool_use") ? "tool_use" : "end_turn",
    usage: {
      input_tokens: u.promptTokenCount ?? 0,
      output_tokens: (u.candidatesTokenCount ?? 0) + (u.thoughtsTokenCount ?? 0),
    },
  };
}
