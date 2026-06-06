#!/usr/bin/env bash
# scripts/m7a/backup-restore-drill.sh
#
# M7a backup-and-restore drill harness (operator-executed).
#
# 1. Takes an out-of-band manual snapshot of the dev RDS instance.
# 2. Restores that snapshot to a new instance with a `-m7a-restore-<ts>`
#    suffix in the same VPC/subnet group.
# 3. Verifies the restored instance reaches `available`.
# 4. Verifies the restored instance is connectable using the same
#    credentials as the source instance (operator confirms via psql; the
#    script prints the endpoint).
# 5. Verifies S3 bucket versioning and lifecycle configuration on the
#    project artifact bucket.
# 6. Optionally cleans up the restored instance.
#
# Operator prerequisites:
#   - aws CLI configured for the same account as Terraform
#   - the operator has rds:CreateDBSnapshot, rds:RestoreDBInstanceFromDBSnapshot,
#     rds:DescribeDBInstances, rds:DeleteDBInstance, s3:GetBucketVersioning,
#     s3:GetLifecycleConfiguration permissions
#
# Usage:
#   scripts/m7a/backup-restore-drill.sh \
#     --project private-ai-workspace \
#     --environment dev \
#     [--keep-restored]
#
# By default the restored instance is deleted at the end of the drill.
# Pass --keep-restored if the operator wants to drive a connectivity test
# against it manually before cleanup.

set -euo pipefail

PROJECT="private-ai-workspace"
ENVIRONMENT="dev"
KEEP=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)        PROJECT="$2"; shift 2 ;;
    --environment)    ENVIRONMENT="$2"; shift 2 ;;
    --keep-restored)  KEEP=1; shift ;;
    -h|--help)
      sed -n '2,35p' "$0"
      exit 0
      ;;
    *) echo "Unknown argument: $1"; exit 2 ;;
  esac
done

require_cmd() {
  command -v "$1" >/dev/null || { echo "ERROR: required command not found: $1"; exit 2; }
}
require_cmd aws
require_cmd jq

INSTANCE_ID="${PROJECT}-${ENVIRONMENT}"
TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"
SNAPSHOT_ID="${INSTANCE_ID}-m7a-${TIMESTAMP}"
RESTORED_ID="${INSTANCE_ID}-m7a-restore-${TIMESTAMP}"

echo "==> Pre-flight: confirm source RDS instance ${INSTANCE_ID} exists"
SOURCE_JSON="$(aws rds describe-db-instances --db-instance-identifier "${INSTANCE_ID}")"
SOURCE_AZ="$(printf '%s' "${SOURCE_JSON}" | jq -r '.DBInstances[0].AvailabilityZone')"
SOURCE_SUBNET_GROUP="$(printf '%s' "${SOURCE_JSON}" | jq -r '.DBInstances[0].DBSubnetGroup.DBSubnetGroupName')"
SOURCE_VPC_SGS="$(printf '%s' "${SOURCE_JSON}" | jq -r '[.DBInstances[0].VpcSecurityGroups[].VpcSecurityGroupId] | join(",")')"
echo "  source AZ:                ${SOURCE_AZ}"
echo "  source subnet group:      ${SOURCE_SUBNET_GROUP}"
echo "  source security groups:   ${SOURCE_VPC_SGS}"

cleanup() {
  if [[ "${KEEP}" -eq 0 ]]; then
    echo ""
    echo "==> Cleanup: delete restored instance ${RESTORED_ID} (skip final snapshot)"
    aws rds delete-db-instance \
      --db-instance-identifier "${RESTORED_ID}" \
      --skip-final-snapshot \
      --delete-automated-backups || true
  else
    echo ""
    echo "==> --keep-restored: leaving ${RESTORED_ID} for manual inspection."
    echo "    Operator must delete it manually after recording connectivity results."
  fi
}
trap cleanup EXIT

START_TS="$(date +%s)"

echo ""
echo "==> Step 1: take out-of-band snapshot ${SNAPSHOT_ID}"
aws rds create-db-snapshot \
  --db-instance-identifier "${INSTANCE_ID}" \
  --db-snapshot-identifier "${SNAPSHOT_ID}" > /dev/null
aws rds wait db-snapshot-completed --db-snapshot-identifier "${SNAPSHOT_ID}"
SNAPSHOT_END="$(date +%s)"
echo "  snapshot complete in $((SNAPSHOT_END - START_TS))s"

echo ""
echo "==> Step 2: restore snapshot to new instance ${RESTORED_ID}"
aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier "${RESTORED_ID}" \
  --db-snapshot-identifier "${SNAPSHOT_ID}" \
  --db-subnet-group-name "${SOURCE_SUBNET_GROUP}" \
  --vpc-security-group-ids ${SOURCE_VPC_SGS//,/ } \
  --no-publicly-accessible \
  --no-multi-az > /dev/null
aws rds wait db-instance-available --db-instance-identifier "${RESTORED_ID}"
RESTORE_END="$(date +%s)"
echo "  restore complete in $((RESTORE_END - SNAPSHOT_END))s"

RESTORED_ENDPOINT="$(aws rds describe-db-instances --db-instance-identifier "${RESTORED_ID}" \
  | jq -r '.DBInstances[0].Endpoint.Address')"
echo "  restored endpoint: ${RESTORED_ENDPOINT}"

echo ""
echo "==> Step 3: verify S3 artifact bucket versioning + lifecycle"
BUCKET="$(aws s3api list-buckets | jq -r --arg p "${PROJECT}" --arg e "${ENVIRONMENT}" \
  '.Buckets[] | select(.Name | startswith($p + "-" + $e)) | .Name' | head -n1)"
if [[ -z "${BUCKET}" ]]; then
  echo "  FAIL  No artifact bucket found matching ${PROJECT}-${ENVIRONMENT}*"
  exit 1
fi
echo "  bucket: ${BUCKET}"

VERSIONING_STATUS="$(aws s3api get-bucket-versioning --bucket "${BUCKET}" \
  | jq -r '.Status // "Disabled"')"
echo "  versioning: ${VERSIONING_STATUS}"
if [[ "${VERSIONING_STATUS}" != "Enabled" ]]; then
  echo "  WARN  Versioning is not Enabled on ${BUCKET}; recommended for prod"
fi

LIFECYCLE_RULES="$(aws s3api get-bucket-lifecycle-configuration --bucket "${BUCKET}" 2>/dev/null \
  | jq -r '.Rules // [] | length' || echo 0)"
echo "  lifecycle rules: ${LIFECYCLE_RULES}"

trap - EXIT
cleanup

echo ""
echo "Backup-and-restore drill PASSED."
echo "Record the following in docs/m7a-report.md → Drill 2:"
echo "  - date:                       $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "  - snapshot id:                ${SNAPSHOT_ID}"
echo "  - restored instance id:       ${RESTORED_ID}"
echo "  - snapshot duration:          $((SNAPSHOT_END - START_TS))s"
echo "  - restore duration:           $((RESTORE_END - SNAPSHOT_END))s"
echo "  - restored endpoint:          ${RESTORED_ENDPOINT}"
echo "  - artifact bucket:            ${BUCKET}"
echo "  - bucket versioning status:   ${VERSIONING_STATUS}"
echo "  - bucket lifecycle rules:     ${LIFECYCLE_RULES}"
echo ""
echo "Operator must additionally:"
echo "  1. Connect to the restored endpoint with the same credentials as the"
echo "     source and run a sentinel SELECT to confirm data integrity."
echo "  2. Delete the M7a snapshot once the report is filed:"
echo "       aws rds delete-db-snapshot --db-snapshot-identifier ${SNAPSHOT_ID}"
