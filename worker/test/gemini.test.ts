import { describe, expect, it } from "vitest";
import { callClaude, modelFor, provider } from "../src/anthropic";
import type { Ctx, Env, Sql } from "../src/env";
import { toContents } from "../src/gemini";
import { Net } from "./helpers";

const reply = (parts: unknown[], extra: Record<string, unknown> = {}) => () => ({
  candidates: [{ content: { role: "model", parts }, finishReason: "STOP" }],
  usageMetadata: { promptTokenCount: 10, candidatesTokenCount: 5, thoughtsTokenCount: 7 },
  ...extra,
});

function ctx(net: Net, env: Partial<Env> = {}): Ctx {
  return {
    sql: {} as Sql,
    fetch: net.fetch as typeof fetch,
    now: () => new Date("2026-11-06T12:00:00Z"),
    env: { GEMINI_API_KEY: "gk", ...env } as Env,
  };
}

describe("izbor provajdera", () => {
  it("gemini ako postoji ključ, inače anthropic; LLM_PROVIDER pregazi", () => {
    expect(provider({ GEMINI_API_KEY: "g" } as Env)).toBe("gemini");
    expect(provider({ ANTHROPIC_API_KEY: "a" } as Env)).toBe("anthropic");
    expect(provider({ GEMINI_API_KEY: "g", LLM_PROVIDER: "anthropic" } as Env)).toBe("anthropic");
  });
  it("modeli po provajderu i pregazivanje", () => {
    expect(modelFor({ GEMINI_API_KEY: "g" } as Env, "agent")).toMatch(/^gemini/);
    expect(modelFor({ GEMINI_API_KEY: "g" } as Env, "rerank")).toMatch(/lite/);
    expect(modelFor({ ANTHROPIC_API_KEY: "a" } as Env, "agent")).toBe("claude-sonnet-5-5");
    expect(modelFor({ AGENT_MODEL: "x" } as Env, "agent")).toBe("x");
  });
});

describe("callClaude preko Gemini-ja", () => {
  it("tekst: Anthropic oblik, tokeni uključuju razmišljanje, ključ u zaglavlju", async () => {
    const net = new Net();
    net.gemini.push(reply([{ text: "zdravo" }]));
    const r = await callClaude(ctx(net), {
      model: "gemini-x",
      system: "sis",
      messages: [{ role: "user", content: "hej" }],
    });
    expect(r.content).toEqual([{ type: "text", text: "zdravo" }]);
    expect(r.stop_reason).toBe("end_turn");
    expect(r.usage).toEqual({ input_tokens: 10, output_tokens: 12 });
    const call = net.geminiCalls[0]!;
    expect(call.url).toContain("/models/gemini-x:generateContent");
    expect(call.body.systemInstruction.parts[0].text).toBe("sis");
    expect(net.claudeCalls).toHaveLength(0);
  });

  it("alat: tool_use sa potpisom, pa functionResponse sa imenom alata", async () => {
    const net = new Net();
    net.gemini.push(
      reply([{ functionCall: { name: "get_profile", args: { a: 1 } }, thoughtSignature: "SIG" }]),
      reply([{ text: "gotovo" }]),
    );
    const c = ctx(net);
    const tools = [{ name: "get_profile", description: "d", input_schema: { type: "object" } }];
    const r1 = await callClaude(c, {
      model: "gemini-x",
      system: "s",
      messages: [{ role: "user", content: "x" }],
      tools,
    });
    const use = r1.content[0]!;
    expect(r1.stop_reason).toBe("tool_use");
    if (use.type !== "tool_use") throw new Error("očekivan tool_use");
    expect(use.thoughtSignature).toBe("SIG");
    expect(net.geminiCalls[0]!.body.tools[0].functionDeclarations[0].parametersJsonSchema).toEqual({
      type: "object",
    });

    await callClaude(c, {
      model: "gemini-x",
      system: "s",
      messages: [
        { role: "user", content: "x" },
        { role: "assistant", content: r1.content },
        { role: "user", content: [{ type: "tool_result", tool_use_id: use.id, content: "{}" }] },
      ],
      tools,
    });
    const sent = net.geminiCalls[1]!.body.contents;
    expect(sent[1].parts[0].thoughtSignature).toBe("SIG");
    expect(sent[2].parts[0].functionResponse).toEqual({ name: "get_profile", response: { output: "{}" } });
  });

  it("greška API-ja i blokiran odgovor bacaju ClaudeError", async () => {
    const net = new Net();
    net.gemini.push(() => new Response("{}", { status: 403 }));
    await expect(
      callClaude(ctx(net), { model: "m", system: "s", messages: [{ role: "user", content: "x" }] }),
    ).rejects.toThrow("Gemini API 403");
    net.gemini.push(() => ({ promptFeedback: { blockReason: "SAFETY" } }));
    await expect(
      callClaude(ctx(net), { model: "m", system: "s", messages: [{ role: "user", content: "x" }] }),
    ).rejects.toThrow("SAFETY");
  });

  it("prazan tekst ne pravi prazan part", () => {
    const out = toContents([{ role: "assistant", content: [{ type: "text", text: "" }] }]);
    expect(out).toEqual([]);
  });
});
