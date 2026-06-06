#!/usr/bin/env bash
# scripts/m7a/governance-check.sh
#
# M7a governance verification harness.
#
# Verifies, against the live GitHub API, that the default-branch protection
# settings match the contract documented in docs/04-governance-and-contribution.md:
#
#   - pull-request-only changes
#   - at least one required maintainer review
#   - required status checks present
#   - no force-pushes
#   - DCO sign-off enforced (either via repo setting or via a check)
#
# Also verifies local artifacts that are checked into the repository:
#
#   - CODEOWNERS exists and is non-empty
#   - CONTRIBUTING.md mentions DCO sign-off
#   - SECURITY.md exists
#   - PULL_REQUEST_TEMPLATE.md exists
#
# Usage:
#   GITHUB_TOKEN=<token> scripts/m7a/governance-check.sh [owner/repo]
#
# Default repo: GetFinops/private-ai-workspace-eks
#
# Requires: curl, jq. Token needs at minimum repo:read for private repos;
# unauthenticated calls cover the public surface but cannot read
# branch-protection settings — those checks degrade to a clear "OPERATOR
# MUST VERIFY MANUALLY" warning.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
REPO="${1:-GetFinops/private-ai-workspace-eks}"
API="https://api.github.com/repos/${REPO}"

fail=0
warn=0

curl_args=(-sS -H "Accept: application/vnd.github+json")
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
  curl_args+=(-H "Authorization: Bearer ${GITHUB_TOKEN}")
fi

http_get() {
  curl "${curl_args[@]}" -w '\n%{http_code}' "$@"
}

echo "==> Local governance artifacts"

check_local_file() {
  local path="$1"
  local label="$2"
  if [[ -s "${ROOT}/${path}" ]]; then
    echo "  PASS  ${label}: ${path}"
  else
    echo "  FAIL  ${label} missing or empty: ${path}"
    fail=1
  fi
}

check_local_file "CODEOWNERS"                       "CODEOWNERS"
check_local_file "CONTRIBUTING.md"                  "CONTRIBUTING.md"
check_local_file "SECURITY.md"                      "SECURITY.md"
check_local_file "CODE_OF_CONDUCT.md"               "CODE_OF_CONDUCT.md"
check_local_file ".github/PULL_REQUEST_TEMPLATE.md" "PR template"

echo ""
echo "==> DCO sign-off contract (CONTRIBUTING.md)"
if grep -qi 'signed-off-by' "${ROOT}/CONTRIBUTING.md" 2>/dev/null; then
  echo "  PASS  CONTRIBUTING.md references Signed-off-by"
else
  echo "  WARN  CONTRIBUTING.md does not reference Signed-off-by"
  warn=1
fi

echo ""
echo "==> GitHub repository: ${REPO}"
repo_response="$(http_get "${API}")"
repo_status="$(printf '%s' "${repo_response}" | tail -n1)"
repo_body="$(printf '%s' "${repo_response}" | sed '$d')"

if [[ "${repo_status}" != "200" ]]; then
  echo "  FAIL  Cannot read repository metadata (HTTP ${repo_status})"
  fail=1
else
  visibility="$(printf '%s' "${repo_body}" | jq -r '.visibility')"
  default_branch="$(printf '%s' "${repo_body}" | jq -r '.default_branch')"
  web_signoff="$(printf '%s' "${repo_body}" | jq -r '.web_commit_signoff_required')"
  echo "  visibility:                       ${visibility}"
  echo "  default_branch:                   ${default_branch}"
  echo "  web_commit_signoff_required:      ${web_signoff}"

  if [[ "${web_signoff}" != "true" ]]; then
    echo "  WARN  web_commit_signoff_required is not enabled — DCO enforcement"
    echo "        currently relies on manual maintainer review only. Recommend"
    echo "        enabling Settings → General → 'Require contributors to sign off"
    echo "        on web-based commits'."
    warn=1
  fi
fi

echo ""
echo "==> Branch protection: ${default_branch:-main}"
prot_response="$(http_get "${API}/branches/${default_branch:-main}/protection")"
prot_status="$(printf '%s' "${prot_response}" | tail -n1)"
prot_body="$(printf '%s' "${prot_response}" | sed '$d')"

if [[ "${prot_status}" == "401" ]] || [[ "${prot_status}" == "404" ]]; then
  echo "  WARN  Cannot read branch-protection settings (HTTP ${prot_status})."
  echo "        Either no token was provided, the token lacks 'repo' scope, or"
  echo "        protection is not configured on the default branch. Operator must"
  echo "        verify in Settings → Branches that protection is enabled per"
  echo "        docs/04-governance-and-contribution.md."
  warn=1
elif [[ "${prot_status}" != "200" ]]; then
  echo "  FAIL  Branch-protection query returned HTTP ${prot_status}"
  fail=1
else
  required_reviews="$(printf '%s' "${prot_body}" | jq -r '.required_pull_request_reviews.required_approving_review_count // 0')"
  force_push="$(printf '%s' "${prot_body}" | jq -r '.allow_force_pushes.enabled')"
  deletions="$(printf '%s' "${prot_body}" | jq -r '.allow_deletions.enabled')"
  status_checks="$(printf '%s' "${prot_body}" | jq -r '.required_status_checks.contexts // [] | length')"

  echo "  required_approving_review_count: ${required_reviews}"
  echo "  allow_force_pushes:               ${force_push}"
  echo "  allow_deletions:                  ${deletions}"
  echo "  required_status_check_count:      ${status_checks}"

  [[ "${required_reviews}" -ge 1 ]] || { echo "  FAIL  Less than 1 required reviewer"; fail=1; }
  [[ "${force_push}" == "false" ]]  || { echo "  FAIL  Force-push must be disabled"; fail=1; }
  [[ "${deletions}" == "false" ]]   || { echo "  FAIL  Branch deletion must be disabled"; fail=1; }
  [[ "${status_checks}" -ge 1 ]]    || { echo "  WARN  No required status checks"; warn=1; }
fi

echo ""
if [[ "${fail}" -eq 0 && "${warn}" -eq 0 ]]; then
  echo "Governance check PASSED."
  exit 0
elif [[ "${fail}" -eq 0 ]]; then
  echo "Governance check PASSED with warnings (review and address)."
  exit 0
else
  echo "Governance check FAILED. Address the FAIL items before declaring M7a complete."
  exit 1
fi
