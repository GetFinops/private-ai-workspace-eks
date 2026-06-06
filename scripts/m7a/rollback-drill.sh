#!/usr/bin/env bash
# scripts/m7a/rollback-drill.sh
#
# M7a rollback drill harness (operator-executed).
#
# Deploys a deliberately broken control-plane image to the dev cluster,
# confirms the readiness gate blocks it, then rolls back to the last known-
# good revision and confirms the service returns to a healthy state.
#
# Operator prerequisites:
#   - kubectl context points at the dev EKS cluster
#   - the control-plane chart is currently deployed and healthy
#   - the operator has cluster-admin or namespace-admin access to `app`
#
# Usage:
#   scripts/m7a/rollback-drill.sh \
#     --release private-ai-workspace \
#     --namespace app \
#     [--bad-image ghcr.io/example/does-not-exist:bad]
#
# The drill is intentionally read-mostly: it does not modify the chart or
# the ECR repository. The broken-image deploy is reversed by `helm rollback`
# before the script exits, regardless of success or failure.

set -euo pipefail

RELEASE="private-ai-workspace"
NAMESPACE="app"
# Default broken image: a tag that does not exist anywhere. The pod will
# fail to pull and the deployment will not become Ready, exercising the
# readiness gate without risking a partially-working bad version.
BAD_IMAGE="ghcr.io/getfinops/private-ai-workspace-eks/intentionally-missing:m7a-rollback-drill"
TIMEOUT_FAIL="60s"
TIMEOUT_ROLLBACK="120s"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --release)   RELEASE="$2"; shift 2 ;;
    --namespace) NAMESPACE="$2"; shift 2 ;;
    --bad-image) BAD_IMAGE="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,30p' "$0"
      exit 0
      ;;
    *) echo "Unknown argument: $1"; exit 2 ;;
  esac
done

require_cmd() {
  command -v "$1" >/dev/null || { echo "ERROR: required command not found: $1"; exit 2; }
}
require_cmd helm
require_cmd kubectl

echo "==> Pre-flight: confirm release is healthy"
helm status "${RELEASE}" --namespace "${NAMESPACE}" > /dev/null
PREVIOUS_REVISION="$(helm list --namespace "${NAMESPACE}" --filter "^${RELEASE}\$" -o json | jq -r '.[0].revision')"
echo "  current revision: ${PREVIOUS_REVISION}"

# Snapshot the running image so the rollback target is unambiguous.
CURRENT_IMAGE="$(kubectl get deployment "${RELEASE}-${RELEASE}" --namespace "${NAMESPACE}" \
  -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)"
echo "  current image:    ${CURRENT_IMAGE:-<unknown>}"

cleanup() {
  echo ""
  echo "==> Cleanup: rolling back to revision ${PREVIOUS_REVISION}"
  helm rollback "${RELEASE}" "${PREVIOUS_REVISION}" --namespace "${NAMESPACE}" --wait --timeout "${TIMEOUT_ROLLBACK}" || true
  kubectl rollout status deployment/"${RELEASE}-${RELEASE}" --namespace "${NAMESPACE}" --timeout="${TIMEOUT_ROLLBACK}" || true
}
trap cleanup EXIT

echo ""
echo "==> Step 1: deploy deliberately broken image ${BAD_IMAGE}"
START_TS="$(date +%s)"
# --wait=false: we expect this to fail; we'll observe the failure ourselves.
helm upgrade "${RELEASE}" deploy/helm/private-ai-workspace \
  --namespace "${NAMESPACE}" \
  --reuse-values \
  --set image.repository="${BAD_IMAGE%:*}" \
  --set image.tag="${BAD_IMAGE##*:}" \
  --wait=false

echo ""
echo "==> Step 2: wait for readiness gate to block (expected to time out)"
if kubectl rollout status deployment/"${RELEASE}-${RELEASE}" \
     --namespace "${NAMESPACE}" --timeout="${TIMEOUT_FAIL}"; then
  echo "  FAIL  rollout reported success against a broken image — readiness gate did not block"
  exit 1
else
  echo "  PASS  readiness gate blocked the broken image as expected"
fi

FAIL_TS="$(date +%s)"
echo "  time to detected failure: $((FAIL_TS - START_TS))s"

echo ""
echo "==> Step 3: rollback (also runs from cleanup trap; explicit here for timing)"
ROLLBACK_START="$(date +%s)"
helm rollback "${RELEASE}" "${PREVIOUS_REVISION}" --namespace "${NAMESPACE}" --wait --timeout "${TIMEOUT_ROLLBACK}"
kubectl rollout status deployment/"${RELEASE}-${RELEASE}" --namespace "${NAMESPACE}" --timeout="${TIMEOUT_ROLLBACK}"
ROLLBACK_END="$(date +%s)"
echo "  time to healthy rollback: $((ROLLBACK_END - ROLLBACK_START))s"

# Disable trap — we already rolled back successfully.
trap - EXIT

echo ""
echo "==> Step 4: verify post-rollback health"
kubectl wait deployment/"${RELEASE}-${RELEASE}" \
  --namespace "${NAMESPACE}" \
  --for=condition=Available \
  --timeout=60s

POST_IMAGE="$(kubectl get deployment "${RELEASE}-${RELEASE}" --namespace "${NAMESPACE}" \
  -o jsonpath='{.spec.template.spec.containers[0].image}')"
if [[ -n "${CURRENT_IMAGE}" && "${POST_IMAGE}" != "${CURRENT_IMAGE}" ]]; then
  echo "  FAIL  post-rollback image ${POST_IMAGE} does not match pre-drill image ${CURRENT_IMAGE}"
  exit 1
fi

echo ""
echo "Rollback drill PASSED."
echo "Record the following in docs/m7a-report.md → Drill 1:"
echo "  - date:                       $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "  - cluster:                    $(kubectl config current-context)"
echo "  - time to detected failure:   $((FAIL_TS - START_TS))s"
echo "  - time to healthy rollback:   $((ROLLBACK_END - ROLLBACK_START))s"
echo "  - pre-drill image:            ${CURRENT_IMAGE:-<unknown>}"
echo "  - post-rollback image:        ${POST_IMAGE}"
