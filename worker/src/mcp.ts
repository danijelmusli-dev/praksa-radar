/** Minimalni MCP server (Streamable HTTP, JSON odgovori) nad alatima radara. */
import type { Ctx } from "./env";
import { TOOLS, TOOL_MAP, type ToolContext } from "./tools";
import { safeEqual } from "./auth";

const PROTOCOL = "2025-03-26";

interface RpcRequest {
  jsonrpc: "2.0";
  id?: number | string | null;
  method: string;
  params?: Record<string, unknown>;
}

const reply = (id: RpcRequest["id"], result: unknown) => ({ jsonrpc: "2.0", id: id ?? null, result });
const fail = (id: RpcRequest["id"], code: number, message: string) => ({
  jsonrpc: "2.0",
  id: id ?? null,
  error: { code, message },
});

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

async function dispatch(c: ToolContext, req: RpcRequest): Promise<unknown | null> {
  switch (req.method) {
    case "initialize":
      return reply(req.id, {
        protocolVersion: PROTOCOL,
        capabilities: { tools: {} },
        serverInfo: { name: "praksa-radar", version: "0.1.0" },
      });
    case "ping":
      return reply(req.id, {});
    case "tools/list":
      return reply(req.id, {
        tools: TOOLS.map((t) => ({
          name: t.spec.name,
          description: t.spec.description,
          inputSchema: t.spec.input_schema,
        })),
      });
    case "tools/call": {
      const name = String(req.params?.name ?? "");
      const tool = TOOL_MAP.get(name);
      if (!tool) return fail(req.id, -32602, `nepoznat alat: ${name}`);
      try {
        const out = await tool.run(c, (req.params?.arguments ?? {}) as Record<string, unknown>);
        return reply(req.id, { content: [{ type: "text", text: JSON.stringify(out) }] });
      } catch (e) {
        const message = e instanceof Error ? e.message : String(e);
        return reply(req.id, { content: [{ type: "text", text: message }], isError: true });
      }
    }
    default:
      return req.method.startsWith("notifications/") ? null : fail(req.id, -32601, "metoda ne postoji");
  }
}

export async function handleMcp(ctx: Ctx, req: Request): Promise<Response> {
  const token = ctx.env.MCP_TOKEN;
  if (!token) return new Response("MCP je isključen", { status: 404 });
  const auth = req.headers.get("authorization") ?? "";
  if (!safeEqual(auth, `Bearer ${token}`)) return new Response("Unauthorized", { status: 401 });
  if (req.method !== "POST") return new Response("Method Not Allowed", { status: 405 });

  let body: RpcRequest | RpcRequest[];
  try {
    body = (await req.json()) as RpcRequest | RpcRequest[];
  } catch {
    return json(fail(null, -32700, "neispravan JSON"), 400);
  }
  const c: ToolContext = { ...ctx, chatId: ctx.env.ALLOWED_CHAT_ID };
  if (Array.isArray(body)) {
    const out = (await Promise.all(body.map((r) => dispatch(c, r)))).filter((r) => r !== null);
    return out.length ? json(out) : new Response(null, { status: 202 });
  }
  const out = await dispatch(c, body);
  return out === null ? new Response(null, { status: 202 }) : json(out);
}
