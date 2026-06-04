#!/usr/bin/env bash
# scripts/bootstrap-infra.sh
#
# Helper wrapper for the infra/terraform first-time bootstrap.
# Runs: init → validate → plan → (optionally) apply.
#
# Usage:
#   ./scripts/bootstrap-infra.sh [--apply] [--env dev|staging|prod]
#
# Prerequisites: AWS CLI configured, Terraform >= 1.6 in PATH.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TERRAFORM_DIR="${SCRIPT_DIR}/../infra/terraform"
ENV="dev"
APPLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --env)   ENV="$2"; shift 2 ;;
    *)       echo "Unknown argument: $1"; exit 1 ;;
  esac
done

VARS_FILE="${SCRIPT_DIR}/../infra/terraform/tfvars/${ENV}.tfvars"

if [[ ! -f "${VARS_FILE}" ]]; then
  echo "ERROR: Variables file not found: ${VARS_FILE}"
  echo ""
  echo "Create infra/terraform/tfvars/${ENV}.tfvars with at minimum:"
  echo "  aws_region   = \"<region>\""
  echo "  environment  = \"${ENV}\""
  echo ""
  exit 1
fi

cd "${TERRAFORM_DIR}"

echo "==> terraform init"
terraform init

echo ""
echo "==> terraform validate"
terraform validate

echo ""
echo "==> terraform plan  (env=${ENV})"
terraform plan -var-file="${VARS_FILE}" -out="${ENV}.tfplan"

if [[ "${APPLY}" -eq 1 ]]; then
  echo ""
  echo "==> terraform apply  (env=${ENV})"
  terraform apply "${ENV}.tfplan"
  echo ""
  echo "==> Cluster outputs"
  terraform output -json | python3 -m json.tool
else
  echo ""
  echo "Plan written to ${ENV}.tfplan"
  echo "Re-run with --apply to provision."
fi
