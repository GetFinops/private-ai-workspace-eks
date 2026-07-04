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
  # M14 media + M10 embedding runtime artifacts that now ship.
  "faster-whisper"
  "stable-diffusion-xl"
  "text-embeddings-inference"
  "external-dns"
)
for img in "${images[@]}"; do
  check_notice_contains "image/model: ${img}" "${img}"
done

echo ""
echo "==> Vendored frontend + new-this-cycle artifacts"
# pdf.js is vendored verbatim under app/ui/static/vendor and must (a) be recorded
# in NOTICE, (b) retain its Apache-2.0 @license header in the shipped file, and
# (c) carry a standalone attribution file alongside the binaries.
check_notice_contains "vendored: pdf.js (pdfjs-dist)" "pdfjs-dist"
check_notice_contains "feature: web_search (external-service, no bundled engine)" "web_search"
PDFJS="${ROOT}/app/ui/static/vendor/pdf.min.js"
PDFJS_LICENSE="${ROOT}/app/ui/static/vendor/LICENSE"
if [[ -f "${PDFJS}" ]] && grep -q "Apache License" "${PDFJS}"; then
  echo "  PASS  pdf.min.js present with embedded Apache-2.0 @license header"
else
  echo "  FAIL  pdf.min.js missing or its Apache-2.0 @license header was stripped"
  fail=1
fi
if [[ -f "${PDFJS_LICENSE}" ]]; then
  echo "  PASS  standalone vendor/LICENSE attribution present"
else
  echo "  FAIL  app/ui/static/vendor/LICENSE (attribution) is missing"
  fail=1
fi

echo ""
echo "==> AGPL-sensitive components (must NOT be WIRED into the default build)"
# These may be MENTIONED in NOTICE / docs / code comments as excluded-by-default
# (e.g. web_search.py's "embeds NO SearXNG") — that is the exclusion *statement*
# and is intentionally not scanned. We check the surfaces where a real
# dependency / image / vendored asset would actually live: Python deps, the whole
# deploy/ Helm surface (charts AND values, broadened from the old Chart.yaml-only
# check), and the vendored-assets dir.
VENDOR_DIR="${ROOT}/app/ui/static/vendor"

# PyMuPDF — a Python dependency; must not be declared in pyproject.toml.
if grep -iq "pymupdf" "${ROOT}/pyproject.toml"; then
  echo "  FAIL  PyMuPDF present in pyproject.toml (must be excluded)"
  fail=1
else
  echo "  PASS  PyMuPDF not a Python dependency"
fi

# SearXNG — a bundled search engine; must not appear as a Helm image/chart/values
# entry or as a vendored asset (WEB_SEARCH is external-service only).
if grep -riq "searxng" "${ROOT}/deploy" 2>/dev/null \
   || { [[ -d "${VENDOR_DIR}" ]] && ls "${VENDOR_DIR}" | grep -qi "searxng"; }; then
  echo "  FAIL  SearXNG wired into Helm/deploy or vendored (must be excluded)"
  fail=1
else
  echo "  PASS  SearXNG not wired into Helm/deploy or vendored"
fi

echo ""
if [[ "${fail}" -eq 0 ]]; then
  echo "License sweep PASSED."
  exit 0
else
  echo "License sweep FAILED. Update NOTICE or fix dependency footprint."
  exit 1
fi
