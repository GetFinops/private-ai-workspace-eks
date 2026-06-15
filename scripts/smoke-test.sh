#!/usr/bin/env bash
# scripts/smoke-test.sh
#
# Smoke test for the control-plane API, covering the M9 product surface (the
# authenticated chat path and the tenant-scoped notifications service) and the
# M10 retrieval + per-user memory surfaces.
#
# The notification + chat routes exercised here are exactly the control-plane
# API the vanilla-JS UI drives with its PKCE-obtained bearer token, so a green
# run validates the surface behind the M9 client. The M10 sections index a
# document and run a retrieval query, and record/recall/delete a memory, then
# probe cross-tenant retrieval isolation (--token-b) and cross-user memory
# isolation (--token-c, a second user in the SAME tenant as --token).
#
# Modes:
#
#   Local (default) — spins up the control plane with the development token
#   verifier (ENVIRONMENT=development + DEV_AUTH_TOKEN) and exercises the full
#   notification round trip, auth gating, content-policy enforcement, and the
#   chat path's degraded response. Self-contained; no cluster required.
#
#       ./scripts/smoke-test.sh [--port 8080]
#
#   Cluster — targets an already-running control plane (e.g. the dev
#   deployment) using real OIDC bearer tokens. Supplying a second token runs
#   the cross-tenant isolation probe required by the M9 exit criteria.
#
#       ./scripts/smoke-test.sh --base https://<control-plane> \
#           --token "$TOKEN_TENANT_A" [--token-b "$TOKEN_TENANT_B"] \
#           [--token-c "$TOKEN_TENANT_A_USER_2"]
#
#   --token-b is a token for a DIFFERENT tenant (different email domain);
#   --token-c is a token for a DIFFERENT user in the SAME tenant as --token.
#
#   --public-only skips every authenticated check (no token required) and
#   probes only the public surface: health, inference status, routing, and
#   that the protected routes reject anonymous access. Used by the deploy
#   pipeline as a post-deploy sanity check.
#
#       ./scripts/smoke-test.sh --base http://localhost:8080 --public-only
#
# Exit status is non-zero if any check fails.

set -euo pipefail

PORT="${PORT:-8080}"
BASE=""
TOKEN=""
TOKEN_B=""
TOKEN_C=""
PUBLIC_ONLY=0
DEV_TOKEN="smoke-dev-token"   # local mode only; accepted by DevTokenVerifier
TIMEOUT=10

usage() { sed -n '2,38p' "$0"; exit "${1:-0}"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)        PORT="$2"; shift 2 ;;
    --base)        BASE="${2%/}"; shift 2 ;;
    --token)       TOKEN="$2"; shift 2 ;;
    --token-b)     TOKEN_B="$2"; shift 2 ;;
    --token-c)     TOKEN_C="$2"; shift 2 ;;
    --public-only) PUBLIC_ONLY=1; shift ;;
    -h|--help)     usage 0 ;;
    *) echo "Unknown argument: $1" >&2; usage 1 ;;
  esac
done

# ── Mode selection ───────────────────────────────────────────────────────────
LOCAL_MODE=1
SERVER_PID=""
SERVER_LOG=""
if [[ -n "${BASE}" ]]; then
  LOCAL_MODE=0
  if [[ -z "${TOKEN}" && "${PUBLIC_ONLY}" -eq 0 ]]; then
    echo "ERROR: --base requires --token <bearer-token> (or --public-only)" >&2
    exit 2
  fi
fi

# Authenticated checks run unless --public-only was requested.
RUN_AUTH=1
if [[ "${PUBLIC_ONLY}" -eq 1 ]]; then RUN_AUTH=0; fi

TMP_BODY="$(mktemp -t smoke-body.XXXXXX)"

if [[ "${LOCAL_MODE}" -eq 1 ]]; then
  BASE="http://localhost:${PORT}"
  TOKEN="${DEV_TOKEN}"
  SERVER_LOG="$(mktemp -t smoke-cp.XXXXXX.log)"
  echo "==> Starting control-plane server on port ${PORT} (development token verifier)"
  # NOTE: the entrypoint reads PORT/HOST from the environment, not argv.
  # M11: enable the agent tool framework with a deny-by-default allow-list that
  # only grants the dev principal (dev@localhost → tenant "localhost") the
  # text_stats stub tool, so both the success and denial paths are exercisable.
  ENVIRONMENT=development DEV_AUTH_TOKEN="${DEV_TOKEN}" PORT="${PORT}" \
    AGENT_TOOLS_ENABLED=true \
    AGENT_TOOLS_ALLOWLIST='{"localhost":["text_stats","text_stats_job"]}' \
    MCP_ENABLED=true \
    MCP_ALLOWLIST='{"localhost":["stub"]}' \
    python3 -m app.control_plane >"${SERVER_LOG}" 2>&1 &
  SERVER_PID=$!
fi

cleanup() {
  if [[ -n "${SERVER_PID}" ]]; then
    echo ""
    echo "==> Stopping server (PID ${SERVER_PID})"
    kill "${SERVER_PID}" 2>/dev/null || true
    wait "${SERVER_PID}" 2>/dev/null || true
  fi
  if [[ -n "${TMP_BODY}" ]]; then rm -f "${TMP_BODY}"; fi
  if [[ -n "${SERVER_LOG}" ]]; then rm -f "${SERVER_LOG}"; fi
  return 0
}
trap cleanup EXIT

# Wait for the target to answer /healthz.
for i in $(seq 1 "${TIMEOUT}"); do
  if curl -sf "${BASE}/healthz" >/dev/null 2>&1; then break; fi
  if [[ "${i}" -eq "${TIMEOUT}" ]]; then
    echo "ERROR: ${BASE} did not become reachable within ${TIMEOUT}s" >&2
    if [[ -n "${SERVER_LOG}" ]]; then echo "--- server log ---"; cat "${SERVER_LOG}"; fi
    exit 1
  fi
  sleep 1
done

fail=0

# ── HTTP + assertion helpers ─────────────────────────────────────────────────

# req METHOD PATH [TOKEN] [BODY]  →  sets RESP_STATUS and RESP_BODY
req() {
  local method="$1" path="$2" token="${3:-}" body="${4:-}"
  local args=(-s -o "${TMP_BODY}" -w '%{http_code}' -X "${method}" "${BASE}${path}")
  if [[ -n "${token}" ]]; then args+=(-H "Authorization: Bearer ${token}"); fi
  if [[ -n "${body}"  ]]; then args+=(-H "Content-Type: application/json" --data "${body}"); fi
  RESP_STATUS="$(curl "${args[@]}")"
  RESP_BODY="$(cat "${TMP_BODY}")"
}

pass()  { echo "  PASS  $1"; }
flunk() { echo "  FAIL  $1"; if [[ -n "${RESP_BODY:-}" ]]; then echo "        body: ${RESP_BODY}"; fi; fail=1; }

# expect_status LABEL EXPECTED
expect_status() {
  if [[ "${RESP_STATUS}" == "$2" ]]; then pass "$1  (${RESP_STATUS})"; else flunk "$1  expected $2, got ${RESP_STATUS}"; fi
}
# expect_status_in LABEL "S1 S2 ..."
expect_status_in() {
  local s
  for s in $2; do
    if [[ "${RESP_STATUS}" == "${s}" ]]; then pass "$1  (${RESP_STATUS})"; return; fi
  done
  flunk "$1  expected one of [$2], got ${RESP_STATUS}"
}

# JSON helpers — tolerant (always exit 0); read RESP_BODY from argv.
json_id() {
  python3 -c 'import sys, json
try:
    print(json.loads(sys.argv[1]).get("id", ""))
except Exception:
    print("")' "${RESP_BODY}"
}
list_has() {  # list_has <id> → "yes" / "no" / "err"
  python3 -c 'import sys, json
try:
    d = json.loads(sys.argv[1])
    ids = [n.get("id") for n in d.get("notifications", [])]
    print("yes" if sys.argv[2] in ids else "no")
except Exception:
    print("err")' "${RESP_BODY}" "$1"
}
id_read() {  # id_read <id> → "True" / "False" / "missing" / "err"
  python3 -c 'import sys, json
try:
    d = json.loads(sys.argv[1])
    m = {n.get("id"): n for n in d.get("notifications", [])}
    n = m.get(sys.argv[2])
    print(n.get("read") if n else "missing")
except Exception:
    print("err")' "${RESP_BODY}" "$1"
}
results_contain() {  # results_contain <text> → "yes" / "no" / "err"  (over results[].content)
  python3 -c 'import sys, json
try:
    d = json.loads(sys.argv[1])
    texts = " ".join(r.get("content", "") for r in d.get("results", []))
    print("yes" if sys.argv[2] in texts else "no")
except Exception:
    print("err")' "${RESP_BODY}" "$1"
}
mem_list_has() {  # mem_list_has <id> → "yes" / "no" / "err"  (over memories[].id)
  python3 -c 'import sys, json
try:
    d = json.loads(sys.argv[1])
    ids = [m.get("id") for m in d.get("memories", [])]
    print("yes" if sys.argv[2] in ids else "no")
except Exception:
    print("err")' "${RESP_BODY}" "$1"
}
tool_result_ok() {  # tool_result_ok → "yes"/"no"/"err"  (M11: result has the text_stats shape)
  python3 -c 'import sys, json
try:
    d = json.loads(sys.argv[1])
    r = d.get("result", {})
    ok = d.get("result_class") == "success" and all(k in r for k in ("characters", "words", "lines"))
    print("yes" if ok else "no")
except Exception:
    print("err")' "${RESP_BODY}"
}
feed_has_class() {  # feed_has_class <event_class> → "yes"/"no"/"err"  (over notifications[].event_class)
  python3 -c 'import sys, json
try:
    d = json.loads(sys.argv[1])
    classes = [n.get("event_class") for n in d.get("notifications", [])]
    print("yes" if sys.argv[2] in classes else "no")
except Exception:
    print("err")' "${RESP_BODY}" "$1"
}
mcp_echo_is() {  # mcp_echo_is <expected> → "yes"/"no"/"err"  (M12: result.content[0].text)
  python3 -c 'import sys, json
try:
    d = json.loads(sys.argv[1])
    print("yes" if d.get("result", {}).get("content", [{}])[0].get("text") == sys.argv[2] else "no")
except Exception:
    print("err")' "${RESP_BODY}" "$1"
}

# ── Core control-plane probes (public) ───────────────────────────────────────
echo ""
echo "==> Core control-plane probes  (${BASE})"
req GET /healthz;             expect_status "/healthz" 200
req GET /v1/inference/status; expect_status "/v1/inference/status" 200
req GET /unknown;             expect_status "/unknown route → 404" 404
if [[ "${LOCAL_MODE}" -eq 1 ]]; then
  req GET /readyz;            expect_status "/readyz (unconfigured → 503)" 503
fi

# ── M9: auth gating (public — protected routes reject anonymous access) ───────
echo ""
echo "==> M9 auth gating"
req GET  /v1/notifications;             expect_status "GET  /v1/notifications  no token → 401"  401
req GET  /v1/notifications "bad-token"; expect_status "GET  /v1/notifications  bad token → 401" 401
req POST /v1/chat/completions "" '{"model":"x","messages":[{"role":"user","content":"hi"}]}'
expect_status "POST /v1/chat/completions  no token → 401" 401
req POST /v1/agent/tools/invoke "" '{"tool":"text_stats","arguments":{"text":"x"}}'
expect_status "POST /v1/agent/tools/invoke  no token → 401" 401
req POST /v1/agent/runs "" '{"task":"x"}'
expect_status "POST /v1/agent/runs  no token → 401" 401
req POST /v1/agent/research "" '{"question":"x"}'
expect_status "POST /v1/agent/research  no token → 401" 401
req POST /v1/mcp/invoke "" '{"server":"stub","tool":"echo","arguments":{"message":"x"}}'
expect_status "POST /v1/mcp/invoke  no token → 401" 401

if [[ "${RUN_AUTH}" -eq 1 ]]; then
  # ── M9: chat path (authenticated) ──────────────────────────────────────────
  echo ""
  echo "==> M9 chat path (authenticated)"
  req POST /v1/chat/completions "${TOKEN}" '{"model":"smoke","messages":[{"role":"user","content":"ping"}]}'
  if [[ "${LOCAL_MODE}" -eq 1 ]]; then
    # No inference backend configured locally → graceful degraded response.
    expect_status "POST /v1/chat/completions  (auth ok, degraded → 503)" 503
  else
    expect_status_in "POST /v1/chat/completions  (auth ok → 200/503)" "200 503"
  fi

  # ── M9: notifications round trip (publish → list → mark-read/dismiss) ───────
  # The dismiss control is a client-side hide plus a best-effort mark-read, so
  # the mark-read path below covers the API behind both controls.
  echo ""
  echo "==> M9 notifications round trip"
  MARKER="smoke-$$-${RANDOM}"
  req POST /v1/notifications "${TOKEN}" "{\"event_class\":\"system_notice\",\"resource_id\":\"${MARKER}\"}"
  expect_status "publish system_notice → 201" 201
  NID="$(json_id)"
  if [[ -n "${NID}" ]]; then pass "publish returned an id  (${NID})"; else flunk "publish returned no id"; fi

  req GET /v1/notifications "${TOKEN}"
  expect_status "list unread → 200" 200
  if [[ "$(list_has "${NID}")" == "yes" ]]; then pass "unread feed contains the published id"; else flunk "published id missing from unread feed"; fi

  # Validation + content-policy guards.
  req POST /v1/notifications "${TOKEN}" "{\"event_class\":\"system_notice\",\"resource_id\":\"x\",\"prompt\":\"secret\"}"
  expect_status "publish carrying prompt content → 400" 400
  req POST /v1/notifications "${TOKEN}" "{\"event_class\":\"not_a_real_class\",\"resource_id\":\"x\"}"
  expect_status "publish with invalid event_class → 422" 422
  req POST /v1/notifications "${TOKEN}" "{\"event_class\":\"system_notice\"}"
  expect_status "publish without resource_id → 400" 400

  # Mark-read (also the API behind the dismiss control).
  req POST "/v1/notifications/${NID}/read" "${TOKEN}"
  expect_status "mark-read the published id → 200" 200

  req GET /v1/notifications "${TOKEN}"
  expect_status "list unread after mark-read → 200" 200
  if [[ "$(list_has "${NID}")" == "no" ]]; then pass "read notification left the unread feed"; else flunk "read notification still in unread feed"; fi

  req GET "/v1/notifications?include_read=true" "${TOKEN}"
  expect_status "list including read → 200" 200
  if [[ "$(id_read "${NID}")" == "True" ]]; then pass "notification shows read=true with include_read"; else flunk "notification not marked read in include_read feed"; fi

  req POST "/v1/notifications/${NID}/read" "${TOKEN}"
  expect_status "re-mark already-read id → 404" 404
  req POST "/v1/notifications/00000000-0000-0000-0000-000000000000/read" "${TOKEN}"
  expect_status "mark-read unknown id → 404" 404

  # ── M9: cross-tenant isolation probe (requires two real identities) ────────
  echo ""
  if [[ -n "${TOKEN_B}" ]]; then
    echo "==> M9 cross-tenant isolation probe (A publishes; B must not see it)"
    req POST /v1/notifications "${TOKEN}" "{\"event_class\":\"system_notice\",\"resource_id\":\"smoke-xtenant-$$-${RANDOM}\"}"
    expect_status "A publishes isolation marker → 201" 201
    AID="$(json_id)"

    req GET "/v1/notifications?include_read=true" "${TOKEN_B}"
    expect_status "B lists notifications → 200" 200
    if [[ "$(list_has "${AID}")" == "no" ]]; then pass "B cannot see A's notification (isolation holds)"; else flunk "ISOLATION LEAK: B sees A's notification id ${AID}"; fi

    req POST "/v1/notifications/${AID}/read" "${TOKEN_B}"
    expect_status "B cannot mark A's notification read → 404" 404
  else
    echo "==> M9 cross-tenant isolation probe — SKIPPED"
    if [[ "${LOCAL_MODE}" -eq 1 ]]; then
      echo "    The development token verifier maps every token to a single principal,"
      echo "    so true cross-tenant isolation cannot be exercised locally. Run against"
      echo "    the dev deployment with two real OIDC users (different email domains):"
      echo ""
      echo "      ./scripts/smoke-test.sh --base https://<control-plane> \\"
      echo "          --token \"\$TOKEN_TENANT_A\" --token-b \"\$TOKEN_TENANT_B\""
    else
      echo "    Pass --token-b \"\$TOKEN_TENANT_B\" (a second tenant's token) to run it."
    fi
    echo "    Store-layer isolation is unit-covered in tests/test_notifications.py."
  fi

  # ── M10: retrieval round trip (index → query) ──────────────────────────────
  echo ""
  echo "==> M10 retrieval round trip (index → query)"
  RDOC="retrieval smoke marker alpha kubernetes pods autoscaling horizontal"
  req POST /v1/retrieval/documents "${TOKEN}" "{\"title\":\"Smoke Doc\",\"content\":\"${RDOC}\"}"
  expect_status "index document → 201" 201
  req POST /v1/retrieval/query "${TOKEN}" '{"query":"kubernetes pods autoscaling","top_k":5}'
  expect_status "retrieval query → 200" 200
  if [[ "$(results_contain "smoke marker alpha")" == "yes" ]]; then pass "query returns the indexed passage"; else flunk "indexed passage not retrieved"; fi
  req POST /v1/retrieval/documents "${TOKEN}" '{"title":"No body"}'
  expect_status "index without content → 400" 400

  # ── M10: cross-tenant retrieval isolation (A indexes; B must not retrieve) ──
  echo ""
  if [[ -n "${TOKEN_B}" ]]; then
    echo "==> M10 cross-tenant retrieval isolation"
    MARK="xtenant-doc-$$-${RANDOM}"
    req POST /v1/retrieval/documents "${TOKEN}" "{\"title\":\"Secret A\",\"content\":\"acme quarterly revenue ${MARK}\"}"
    expect_status "A indexes a document → 201" 201
    req POST /v1/retrieval/query "${TOKEN_B}" "{\"query\":\"acme quarterly revenue ${MARK}\"}"
    expect_status "B retrieval query → 200" 200
    if [[ "$(results_contain "${MARK}")" == "no" ]]; then pass "B cannot retrieve A's document (isolation holds)"; else flunk "ISOLATION LEAK: B retrieved A's document marker ${MARK}"; fi
  else
    echo "==> M10 cross-tenant retrieval isolation — SKIPPED (pass --token-b)"
  fi

  # ── M10: memory round trip (consent → record → recall → list → delete) ─────
  echo ""
  echo "==> M10 memory round trip (record → recall → delete)"
  req POST /v1/memory "${TOKEN}" '{"content":"no consent given"}'
  expect_status "record without consent → 403" 403
  MARK="mem-$$-${RANDOM}"
  req POST /v1/memory "${TOKEN}" "{\"content\":\"my smoke memory ${MARK}\",\"consent\":true}"
  expect_status "record memory (with consent) → 201" 201
  MEM_ID="$(json_id)"
  if [[ -n "${MEM_ID}" ]]; then pass "record returned an id  (${MEM_ID})"; else flunk "record returned no id"; fi
  req POST /v1/memory/recall "${TOKEN}" "{\"query\":\"smoke memory ${MARK}\"}"
  expect_status "memory recall → 200" 200
  if [[ "$(results_contain "${MARK}")" == "yes" ]]; then pass "recall returns the stored memory"; else flunk "stored memory not recalled"; fi
  req GET /v1/memory "${TOKEN}"
  expect_status "list memories → 200" 200
  if [[ "$(mem_list_has "${MEM_ID}")" == "yes" ]]; then pass "memory appears in the list"; else flunk "memory missing from list"; fi
  req DELETE "/v1/memory/${MEM_ID}" "${TOKEN}"
  expect_status "delete memory → 200" 200
  req POST /v1/memory/recall "${TOKEN}" "{\"query\":\"smoke memory ${MARK}\"}"
  if [[ "$(results_contain "${MARK}")" == "no" ]]; then pass "deleted memory is no longer recalled (authoritative)"; else flunk "deleted memory still recalled"; fi
  req DELETE "/v1/memory/${MEM_ID}" "${TOKEN}"
  expect_status "re-delete already-deleted memory → 404" 404

  # ── M10: cross-user memory isolation (same tenant, different user) ─────────
  echo ""
  if [[ -n "${TOKEN_C}" ]]; then
    echo "==> M10 cross-user memory isolation (A records; C same-tenant must not recall)"
    MARK="xuser-$$-${RANDOM}"
    req POST /v1/memory "${TOKEN}" "{\"content\":\"private note ${MARK}\",\"consent\":true}"
    expect_status "A records a private memory → 201" 201
    AID="$(json_id)"
    req POST /v1/memory/recall "${TOKEN_C}" "{\"query\":\"private note ${MARK}\"}"
    expect_status "C recall → 200" 200
    if [[ "$(results_contain "${MARK}")" == "no" ]]; then pass "C cannot recall A's memory (isolation holds)"; else flunk "ISOLATION LEAK: C recalled A's memory marker ${MARK}"; fi
    req DELETE "/v1/memory/${AID}" "${TOKEN_C}"
    expect_status "C cannot delete A's memory → 404" 404
  else
    echo "==> M10 cross-user memory isolation — SKIPPED"
    echo "    Pass --token-c \"\$TOKEN_USER_C\" (a second user in the SAME tenant as --token) to run it."
    echo "    Store-layer isolation is unit-covered in tests/test_memory.py."
  fi

  # ── M11: agent tool framework (invoke → denied → notification) ─────────────
  # The allow-listed principal invokes the sandboxed text_stats stub; a
  # not-allow-listed tool is rejected (deny-by-default); a successful run emits
  # an agent_task_completed notification to the caller's feed. The sandbox runs
  # the tool out-of-process with scrubbed env + rlimits (unit-covered in
  # tests/test_agent_tools.py); this section validates the live wired surface.
  echo ""
  echo "==> M11 agent tool framework (sandboxed invoke → denied → notification)"
  req POST /v1/agent/tools/invoke "${TOKEN}" '{"tool":"text_stats","arguments":{"text":"hello world\nsecond line"}}'
  expect_status "invoke text_stats (allow-listed) → 200" 200
  if [[ "$(tool_result_ok)" == "yes" ]]; then pass "sandboxed tool returned a text_stats result"; else flunk "invoke did not return a success result_class + stats"; fi

  # M11 Job-sandbox: a job-executor tool routes to the tool-runner dispatcher.
  # End-to-end needs the dispatcher + cluster; with none wired it must degrade
  # cleanly (502 tool_error), never 500/crash. The Job isolation guarantees are
  # unit-covered in tests/test_job_sandbox.py and validated live on dev.
  req POST /v1/agent/tools/invoke "${TOKEN}" '{"tool":"text_stats_job","arguments":{"text":"hi"}}'
  if [[ "${LOCAL_MODE}" -eq 1 ]]; then
    expect_status "invoke job-tool, no dispatcher → 502 (clean)" 502
  else
    expect_status_in "invoke job-tool (200 wired / 502 unwired)" "200 502"
  fi

  req POST /v1/agent/tools/invoke "${TOKEN}" '{"tool":"shell","arguments":{"cmd":"id"}}'
  expect_status "invoke non-allow-listed tool (deny-by-default) → 403" 403

  req POST /v1/agent/tools/invoke "${TOKEN}" '{"tool":"text_stats","arguments":{}}'
  expect_status "invoke with missing required arg → 400" 400

  # A successful invoke must surface an agent_task_completed notification.
  req GET "/v1/notifications?include_read=true" "${TOKEN}"
  expect_status "list notifications after invoke → 200" 200
  if [[ "$(feed_has_class "agent_task_completed")" == "yes" ]]; then pass "invoke emitted an agent_task_completed notification"; else flunk "no agent_task_completed notification in the feed"; fi

  # ── M11: cross-tenant tool isolation (B is not allow-listed → denied) ───────
  echo ""
  if [[ -n "${TOKEN_B}" ]]; then
    echo "==> M11 cross-tenant tool isolation (B not allow-listed → denied)"
    req POST /v1/agent/tools/invoke "${TOKEN_B}" '{"tool":"text_stats","arguments":{"text":"x"}}'
    expect_status "B invokes text_stats (not allow-listed) → 403" 403
  else
    echo "==> M11 cross-tenant tool isolation — SKIPPED (pass --token-b)"
    echo "    Deny-by-default allow-list is unit-covered in tests/test_agent_tools.py."
  fi

  # ── M11: agent loop (plan→act→observe over allow-listed tools) ─────────────
  # The loop drives tools via the LLM. End-to-end runs need the vLLM inference
  # plane (GPU); the loop's authorization, budgets, and injection defenses are
  # unit-covered in tests/test_agent_loop.py. Here we validate the live wired
  # surface degrades cleanly: with no inference reachable it must refuse (503)
  # or fail (502) — never 500/crash. With vLLM up it returns 200.
  echo ""
  echo "==> M11 agent loop (wired surface; inference-dependent)"
  req POST /v1/agent/runs "${TOKEN}" '{"task":"count the words in: hello world"}'
  if [[ "${LOCAL_MODE}" -eq 1 ]]; then
    # Local server has no INFERENCE_BASE_URL → clean cold refusal.
    expect_status "agent run, inference cold → 503 (clean refuse)" 503
  else
    expect_status_in "agent run (wired; 200 up / 502 unreachable / 503 cold)" "200 502 503"
  fi

  # M11 deep-research (plan→retrieve→synthesize over the tenant's M10 corpus).
  # Needs inference (GPU) for e2e; the gating + clean degradation validate here.
  req POST /v1/agent/research "${TOKEN}" '{"question":"how do pods autoscale?"}'
  if [[ "${LOCAL_MODE}" -eq 1 ]]; then
    expect_status "deep-research, inference cold → 503 (clean refuse)" 503
  else
    expect_status_in "deep-research (wired; 200 up / 502 unreachable / 503 cold)" "200 502 503"
  fi

  # ── M12: MCP integration (sandboxed stub server, no inference needed) ───────
  echo ""
  echo "==> M12 MCP integration (allow-listed stub server)"
  req POST /v1/mcp/tools/list "${TOKEN}" '{"server":"stub"}'
  expect_status "mcp tools/list (allow-listed) → 200" 200
  req POST /v1/mcp/invoke "${TOKEN}" '{"server":"stub","tool":"echo","arguments":{"message":"smoke-mcp"}}'
  expect_status "mcp invoke echo → 200" 200
  if [[ "$(mcp_echo_is "smoke-mcp")" == "yes" ]]; then pass "sandboxed MCP server echoed the message"; else flunk "MCP echo did not round-trip"; fi
  req POST /v1/mcp/invoke "${TOKEN}" '{"server":"stub","tool":"ghost","arguments":{}}'
  expect_status "mcp invoke unknown tool → 404" 404

  # ── M12: cross-tenant MCP isolation (B not allow-listed → denied) ──────────
  echo ""
  if [[ -n "${TOKEN_B}" ]]; then
    echo "==> M12 cross-tenant MCP isolation"
    req POST /v1/mcp/invoke "${TOKEN_B}" '{"server":"stub","tool":"echo","arguments":{"message":"x"}}'
    expect_status "B invokes stub (not allow-listed) → 403" 403
  else
    echo "==> M12 cross-tenant MCP isolation — SKIPPED (pass --token-b)"
    echo "    Deny-by-default allow-list is unit-covered in tests/test_mcp.py."
  fi

  # ── M11: operator kill-switch (local mode only) ────────────────────────────
  # The kill-switch is a deploy-time toggle (AGENT_TOOLS_ENABLED), so it can't
  # be flipped against a live deployment mid-run. In local mode we bring up a
  # throwaway control plane with tools DISABLED and confirm invoke → 503.
  echo ""
  if [[ "${LOCAL_MODE}" -eq 1 ]]; then
    echo "==> M11 operator kill-switch (tools disabled → 503)"
    KS_PORT=$((PORT + 1))
    KS_LOG="$(mktemp -t smoke-ks.XXXXXX.log)"
    ENVIRONMENT=development DEV_AUTH_TOKEN="${DEV_TOKEN}" PORT="${KS_PORT}" \
      AGENT_TOOLS_ENABLED=false \
      python3 -m app.control_plane >"${KS_LOG}" 2>&1 &
    KS_PID=$!
    for i in $(seq 1 "${TIMEOUT}"); do
      if curl -sf "http://localhost:${KS_PORT}/healthz" >/dev/null 2>&1; then break; fi
      sleep 1
    done
    KS_STATUS="$(curl -s -o /dev/null -w '%{http_code}' -X POST \
      -H "Authorization: Bearer ${DEV_TOKEN}" -H "Content-Type: application/json" \
      --data '{"tool":"text_stats","arguments":{"text":"x"}}' \
      "http://localhost:${KS_PORT}/v1/agent/tools/invoke")"
    if [[ "${KS_STATUS}" == "503" ]]; then pass "kill-switch: invoke with tools disabled → 503"; else flunk "kill-switch: expected 503, got ${KS_STATUS}"; fi
    # M12: the throwaway server also has MCP disabled (MCP_ENABLED unset).
    MCP_KS="$(curl -s -o /dev/null -w '%{http_code}' -X POST \
      -H "Authorization: Bearer ${DEV_TOKEN}" -H "Content-Type: application/json" \
      --data '{"server":"stub","tool":"echo","arguments":{"message":"x"}}' \
      "http://localhost:${KS_PORT}/v1/mcp/invoke")"
    if [[ "${MCP_KS}" == "503" ]]; then pass "kill-switch: MCP invoke with MCP disabled → 503"; else flunk "MCP kill-switch: expected 503, got ${MCP_KS}"; fi
    kill "${KS_PID}" 2>/dev/null || true
    wait "${KS_PID}" 2>/dev/null || true
    rm -f "${KS_LOG}"
  else
    echo "==> M11 operator kill-switch — SKIPPED (deploy-time toggle; not flippable live)"
    echo "    Kill-switch is unit-covered in tests/test_agent_tools.py; verify in a"
    echo "    deployment by setting config.agentToolsEnabled=false and re-checking 503."
  fi
else
  echo ""
  echo "==> M9 authenticated checks — SKIPPED (--public-only)"
  echo "    Probed the public surface only (health + anonymous-access rejection)."
  echo "    Run the authenticated round trip with a real bearer token:"
  echo ""
  echo "      ./scripts/smoke-test.sh --base ${BASE} --token \"\$TOKEN\" [--token-b \"\$TOKEN_B\"]"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
if [[ "${fail}" -eq 0 ]]; then
  echo "All smoke tests passed."
else
  echo "One or more smoke tests FAILED."
  if [[ -n "${SERVER_LOG}" ]]; then echo "--- server log (tail) ---"; tail -n 20 "${SERVER_LOG}"; fi
  exit 1
fi
