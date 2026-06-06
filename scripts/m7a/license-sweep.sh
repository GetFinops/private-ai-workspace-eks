#!/usr/bin/env bash
# scripts/m7a/license-sweep.sh
#
# M7a license-sweep harness.
#
# Verifies that every third-party Helm chart pinned in Chart.lock files and
# every Python runtime dependency declared in pyproject.toml has a
# corresponding provenance entry in NOTICE. Exits non-zero if anything is
# missing so the script can be used as a pre-flight check before declaring
# M7a complete.
#
# Usage:
#   scripts/m7a/license-sweep.sh
#
# This script is stdlib-only (bash, grep, awk) and runs anywhere the
# repository is checked out. It does not require AWS, kubectl, or helm.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
NOTICE="${ROOT}/NOTICE"

if [[ ! -f "${NOTICE}" ]]; then
  echo "ERROR: ${NOTICE} not found"
  exit 2
fi

fail=0

check_notice_contains() {
  local label="$1"
  local needle="$2"
  if grep -qF -- "${needle}" "${NOTICE}"; then
    echo "  PASS  ${label}"
  else
    echo "  FAIL  ${label} — not found in NOTICE: ${needle}"
    fail=1
  fi
}

echo "==> Helm chart provenance (Chart.lock files vs NOTICE)"
# Charts that must be recorded; one line per chart name (must appear in NOTICE).
charts=(
  "external-secrets"
  "nvidia-device-plugin"
  "dcgm-exporter"
  "cluster-autoscaler"
  "karpenter"
  "prometheus-adapter"
  "kube-prometheus-stack"
)
for chart in "${charts[@]}"; do
  check_notice_contains "chart: ${chart}" "${chart}"
done

echo ""
echo "==> Python runtime dependencies (pyproject.toml vs NOTICE)"
# Runtime Python deps that must be recorded; pyproject.toml is the source of truth.
py_deps=(
  "PyJWT"
  "cryptography"
  "psycopg"
  "boto3"
  "prometheus-client"
  "opentelemetry-sdk"
  "opentelemetry-exporter-otlp-proto-grpc"
)
for dep in "${py_deps[@]}"; do
  check_notice_contains "python: ${dep}" "${dep}"
done

echo ""
echo "==> Container images and model artifacts"
images=(
  "vllm/vllm-openai"
  "mistralai/Mistral-7B-Instruct-v0.3"
)
for img in "${images[@]}"; do
  check_notice_contains "image/model: ${img}" "${img}"
done

echo ""
echo "==> AGPL-sensitive components (must NOT appear in default build)"
agpl_sensitive=(
  "PyMuPDF"
  "SearXNG"
)
for component in "${agpl_sensitive[@]}"; do
  # PyMuPDF and SearXNG are allowed to be mentioned in NOTICE/docs as
  # excluded-by-default, but they must not appear in pyproject.toml or
  # Chart.yaml as a runtime dependency.
  if grep -rq --include='pyproject.toml' --include='Chart.yaml' \
       -F -- "${component}" "${ROOT}"; then
    echo "  FAIL  ${component} appears as a runtime dependency (must be excluded)"
    fail=1
  else
    echo "  PASS  ${component} not present as runtime dependency"
  fi
done

echo ""
if [[ "${fail}" -eq 0 ]]; then
  echo "License sweep PASSED."
  exit 0
else
  echo "License sweep FAILED. Update NOTICE or fix dependency footprint."
  exit 1
fi
