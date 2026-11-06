import type { Ctx } from "./env";

export interface ToolSpec {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
}

export type Block =
  | { type: "text"; text: string }
  | { type: "tool_use"; id: string; name: string; input: Record<string, unknown> }
  | { type: "tool_result"; tool_use_id: string; content: string; is_error?: boolean };

export interface Msg {
  role: "user" | "assistant";
  content: string | Block[];
}

export interface ClaudeResponse {
  content: Block[];
  stop_reason: string;
  usage: { input_tokens: number; output_tokens: number };
}

export class ClaudeError extends Error {}

export async function callClaude(
  ctx: Ctx,
  opts: { model: string; system: string; messages: Msg[]; tools?: ToolSpec[]; maxTokens?: number },
): Promise<ClaudeResponse> {
  const res = await ctx.fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": ctx.env.ANTHROPIC_API_KEY,
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify({
      model: opts.model,
      max_tokens: opts.maxTokens ?? 1024,
      system: [{ type: "text", text: opts.system, cache_control: { type: "ephemeral" } }],
      messages: opts.messages,
      ...(opts.tools?.length ? { tools: opts.tools } : {}),
    }),
  });
  if (!res.ok) throw new ClaudeError(`Anthropic API ${res.status}`);
  return (await res.json()) as ClaudeResponse;
}

export function textOf(blocks: Block[]): string {
  return blocks
    .filter((b): b is Extract<Block, { type: "text" }> => b.type === "text")
    .map((b) => b.text)
    .join("")
    .trim();
}

export function extractJson(text: string): unknown {
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start === -1 || end < start) throw new Error("odgovor ne sadrži JSON objekat");
  return JSON.parse(text.slice(start, end + 1));
}
