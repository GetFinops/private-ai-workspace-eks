# M12 — MCP integration design

> Escalation-gate / design artifact for
> [`milestones/m12-mcp-integration.md`](milestones/m12-mcp-integration.md). M12 is
> "standard" risk and **depends on M11** — MCP servers are a constrained form of
> tool and run inside the reviewed M11 sandbox. The adoption decision and the
> per-server record are in `NOTICE` ("M12 MCP adoption decision"). Per the
> milestone, **adopting any individual MCP server is its own escalation trigger**;
> this increment ships only a pure stub server.

## What MCP is, and how it maps here

The Model Context Protocol is JSON-RPC 2.0 between a client (the agent) and an
**MCP server** that exposes `tools` (and resources/prompts). The methods we use:
`initialize` → `notifications/initialized` → `tools/list` → `tools/call`. The
stdio transport is newline-delimited JSON-RPC over the server's stdin/stdout.

That maps cleanly onto M11: **an MCP server is a sandboxed subprocess** the
control plane spawns and speaks JSON-RPC to over stdio — the same out-of-process,
scrubbed-env, rlimited, timed-out, killpg envelope as the M11 subprocess sandbox.
No new execution model is introduced (milestone build task 1).

## Architecture

```
control plane ──(per call: spawn in M11 sandbox)──► MCP SERVER subprocess (stdio JSON-RPC)
  - per-tenant allow-list (deny by default)            scrubbed env + ONLY this tenant's creds
  - per-tenant credential scoping (fetch → inject)     RLIMIT cpu/mem/fsize/nofile, timeout+killpg
  - audit (shape only) + kill-switch                   initialize → tools/list | tools/call → exit
```

- **Connection per call, not pooled.** Each `tools/list` / `tools/call` spawns a
  **fresh** server process scoped to the caller's tenant, does the minimal
  JSON-RPC exchange, and exits. There is **no shared connection across tenants**,
  so cross-tenant session leakage is impossible by construction (milestone build
  task 4). (Pooling is a later optimization with its own isolation review.)
- **Per-tenant allow-list (deny by default).** `MCP_ALLOWLIST` JSON
  `{"<tenant>": ["<server>", ...]}`. A server not allow-listed for the caller's
  tenant — or unknown — is rejected before any spawn and audited. The tenant
  comes from the verified token, never the body.
- **Per-tenant credential scoping.** A server that needs credentials declares a
  secret key; the framework resolves it from managed secret storage under a
  **tenant-scoped name** and injects it into **that one process's** env only —
  never a shared/ambient env var (milestone build task 3, security gate). The
  stub server needs none; the resolver is a no-op until a real server is adopted.
- **Audit (shape only).** Every MCP invocation logs tenant, server, tool,
  sanitised argument shape, result class, latency — never argument or result
  content (M5).
- **Operator kill-switch.** `MCP_ENABLED` (default off) disables all MCP
  integrations cluster-wide; when off the endpoints return 503 and nothing spawns.
- **Network egress** from MCP servers follows the M11 allow-list policy: the stub
  needs none; a server that needs egress requires the Job sandbox + an egress
  allow-list + its own review (gated on issue #36 for cluster enforcement).

## Endpoints
- `POST /v1/mcp/tools/list` `{ "server": "<name>" }` → the server's tools.
- `POST /v1/mcp/invoke` `{ "server", "tool", "arguments" }` → call a tool.

Both reuse the M11 authorization shape: verify token → tenant; kill-switch;
deny-by-default allow-list; per-tenant rate/concurrency limit; sandboxed spawn;
audit.

## The dev validation server (per-server gate)
Ships exactly one **pure stub MCP server** (`app/mcp_servers/stub_server.py`):
stdlib-only, stdio JSON-RPC, **no network and no credentials**. It exposes one
inert tool (`echo`). This satisfies the milestone's "pick at least one MCP server"
and "no real external network reach in dev" with the lowest possible risk — there
is nothing to license-review or credential-scope. Any real MCP server is a
separate adoption decision + per-server `NOTICE` record + licensing/isolation
review, and (if it needs egress/creds) the Job sandbox.

## Testing & validation (the bar)
- The stub server runs sandboxed; `tools/list` and `tools/call` work end-to-end.
- A second tenant (not allow-listed) is rejected and audit-logged (cross-tenant).
- Kill-switch off → 503, nothing spawned.
- Malformed/oversized server output, server crash, and timeout map to clean
  error classes (never a control-plane crash).
- Credential scoping: a server's secret is resolved per-tenant and injected only
  into its own process (unit-covered; the stub exercises the no-cred path).

## Non-goals / red lines
Arbitrary MCP server adoption (each is its own decision); pooled/long-lived
cross-tenant connections; secrets via shared env; MCP-exposed personal-info
integrations (M13). The sandbox is not relaxed.
