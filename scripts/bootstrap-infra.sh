#!/usr/bin/env bash
# scripts/bootstrap-infra.sh
#
# Helper wrapper for the infra/terraform first-time bootstrap.
# Runs: init → validate → plan → (optionally) apply.
#
# On the first run (no EKS cluster yet) the helm/kubernetes providers
# cannot connect to a cluster that doesn't exist yet.  We solve this
# with a two-phase apply:
#
#   Phase 1 (-target): VPC + EKS cluster + all cluster-level AWS
#            resources.  After this phase the cluster endpoint is known
#            and the providers can authenticate via the exec block.
#   Phase 2:           Everything else (Helm releases, IRSA roles, RDS,
#            S3, ECR, …).
#
# Subsequent runs (cluster already exists) skip Phase 1 and go straight
# to a normal plan/apply.
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

# ── Detect whether the EKS cluster already exists in state ───────────────────
CLUSTER_IN_STATE=$(terraform state list 2>/dev/null \
  | grep -c 'module\.eks\.module\.eks\.aws_eks_cluster' || true)

if [[ "${APPLY}" -eq 1 ]] && [[ "${CLUSTER_IN_STATE}" -eq 0 ]]; then
  echo ""
  echo "==> Fresh deployment detected — running two-phase apply"
  echo ""

  # Phase 1: provision the network and EKS control plane so the
  # kubernetes/helm providers can authenticate for Phase 2.
  echo "==> Phase 1: VPC + EKS cluster (env=${ENV})"
  terraform apply \
    -var-file="${VARS_FILE}" \
    -target=module.vpc \
    -target=module.eks \
    -auto-approve

  echo ""
  echo "==> Phase 2: remaining resources (env=${ENV})"
  terraform plan -var-file="${VARS_FILE}" -out="${ENV}.tfplan"
  terraform apply "${ENV}.tfplan"

else
  echo ""
  echo "==> terraform plan  (env=${ENV})"
  terraform plan -var-file="${VARS_FILE}" -out="${ENV}.tfplan"

  if [[ "${APPLY}" -eq 1 ]]; then
    echo ""
    echo "==> terraform apply  (env=${ENV})"
    terraform apply "${ENV}.tfplan"
  else
    echo ""
    echo "Plan written to ${ENV}.tfplan"
    echo "Re-run with --apply to provision."
  fi
fi

if [[ "${APPLY}" -eq 1 ]]; then
  echo ""
  echo "==> Cluster outputs"
  terraform output -json | python3 -m json.tool
fi
