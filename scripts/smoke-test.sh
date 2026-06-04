#!/usr/bin/env bash
# scripts/smoke-test.sh
#
# Quick local smoke test for the control-plane service.
# Starts the server, probes the health endpoints, then stops the server.
#
# Usage:
#   ./scripts/smoke-test.sh [--port 8080]

set -euo pipefail

PORT="${PORT:-8080}"
BASE="http://localhost:${PORT}"
TIMEOUT=10

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; BASE="http://localhost:${PORT}"; shift 2 ;;
    *)      echo "Unknown argument: $1"; exit 1 ;;
  esac
done

echo "==> Starting control-plane server on port ${PORT}"
python3 -m app.control_plane --port "${PORT}" &
SERVER_PID=$!

cleanup() {
  echo ""
  echo "==> Stopping server (PID ${SERVER_PID})"
  kill "${SERVER_PID}" 2>/dev/null || true
}
trap cleanup EXIT

# Wait for the server to come up.
for i in $(seq 1 "${TIMEOUT}"); do
  if curl -sf "${BASE}/healthz" > /dev/null 2>&1; then
    break
  fi
  if [[ "${i}" -eq "${TIMEOUT}" ]]; then
    echo "ERROR: Server did not start within ${TIMEOUT}s"
    exit 1
  fi
  sleep 1
done

fail=0

check() {
  local label="$1"
  local url="$2"
  local expected_status="$3"
  local http_status
  http_status=$(curl -s -o /dev/null -w "%{http_code}" "${url}")
  if [[ "${http_status}" == "${expected_status}" ]]; then
    echo "  PASS  ${label}  (${http_status})"
  else
    echo "  FAIL  ${label}  expected ${expected_status}, got ${http_status}"
    fail=1
  fi
}

echo ""
echo "==> Probing ${BASE}"
check "/healthz → 200"             "${BASE}/healthz"             200
check "/readyz → 503 (unconfigured)" "${BASE}/readyz"              503
check "/v1/inference/status → 200" "${BASE}/v1/inference/status" 200
check "/unknown → 404"             "${BASE}/unknown"             404

echo ""
if [[ "${fail}" -eq 0 ]]; then
  echo "All smoke tests passed."
else
  echo "One or more smoke tests failed."
  exit 1
fi
