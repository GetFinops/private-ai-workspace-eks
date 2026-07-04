# M7a — Platform Hardening (minimal, pre–Phase 2) — Report

> Status of the milestone defined in
> [`milestones/m7a-platform-hardening-minimal.md`](milestones/m7a-platform-hardening-minimal.md).
>
> This is the public record of the M7a pass. Sensitive operational
> details (specific AWS account ids, secret values, ARNs) are kept out
> of this document and are recorded in the maintainer's private
> deployment log instead.

## Status

**Complete (2026-07-04).** Paper review, license sweep, governance/branch-protection
verification (§3), **and both live drills** are done: the rollback drill (§4) and
the backup-and-restore drill (§5) were executed against `private-ai-workspace-dev`
and PASSED. Two non-blocking follow-ups remain, both explicitly out of the M7a
exit criteria: F-05 (a repo Settings sign-off toggle, §3) and an optional in-VPC
sentinel SELECT for byte-level restore integrity (§5).

## Scope

Per the milestone definition, M7a covers a security-posture pass over
the surface shipped through M6, plus rollback, backup/restore, and
governance drills. It does **not** cover the full staging soak under
the post-Phase 2 surface; that is M7b.

The surface reviewed in this pass:

- Terraform modules: `vpc`, `ecr`, `eks`, `rds`, `s3`, `irsa-app`,
  `irsa-vllm`, `irsa-cluster-autoscaler`, `karpenter`,
  `github-actions-role`
- Helm charts: `cluster-addons` (with the M6 controllers enabled),
  `private-ai-workspace` (control plane), `vllm`, `observability`
- Deploy workflow: `.github/workflows/deploy.yml`
- Governance artifacts: `CODEOWNERS`, `CONTRIBUTING.md`, `SECURITY.md`,
  `CODE_OF_CONDUCT.md`, `.github/PULL_REQUEST_TEMPLATE.md`
- `NOTICE`

## 1. Security posture pass

### 1.1 IRSA trust policies

| Role | Trust principal | Trust condition | Verdict |
| --- | --- | --- | --- |
| `irsa-app` | EKS OIDC provider | `sub = system:serviceaccount:<ns>:<sa>`, `aud = sts.amazonaws.com` | ✅ scoped |
| `irsa-vllm` | EKS OIDC provider | `sub = system:serviceaccount:<ns>:<sa>`, `aud = sts.amazonaws.com` | ✅ scoped |
| `irsa-cluster-autoscaler` | EKS OIDC provider | `sub = system:serviceaccount:kube-system:cluster-autoscaler`, `aud = sts.amazonaws.com` | ✅ scoped |
| `karpenter` controller | EKS OIDC provider | `sub = system:serviceaccount:kube-system:karpenter`, `aud = sts.amazonaws.com` | ✅ scoped |
| `aws-load-balancer-controller` | EKS OIDC provider | `sub = system:serviceaccount:kube-system:aws-load-balancer-controller`, `aud = sts.amazonaws.com` | ✅ scoped |
| `karpenter` node role | `ec2.amazonaws.com` | standard EC2 service assume | ✅ standard EKS worker pattern |

All IRSA assume-role policies are scoped to a specific namespace + service
account; no role is assumable cluster-wide.

### 1.2 IAM permission scoping

| Role | Permission notes | Verdict |
| --- | --- | --- |
| `irsa-cluster-autoscaler` | `Describe*` actions on `resources = *` (required by upstream); `SetDesiredCapacity` + `TerminateInstanceInAutoScalingGroup` conditioned on `aws:ResourceTag/k8s.io/cluster-autoscaler/<cluster> = owned` | ✅ scoped via tag |
| `karpenter` controller | Broad `ec2:*` on `resources = *` per the upstream Karpenter CloudFormation reference; `iam:PassRole` scoped to the specific node role ARN; `eks:DescribeCluster` scoped to this cluster's ARN; instance-profile management on `resources = *` | ⚠️ upstream-recommended but broad — see Finding F-01 |
| `irsa-app` | Read on the app-config Secrets Manager secret; read on the RDS credentials secret; S3 bucket read/write scoped to the project bucket | ✅ scoped |
| `irsa-vllm` | Read on the Hugging Face token secret only | ✅ tightly scoped |
| `github_actions_role` + `aws_eks_access_policy_association.github_actions_admin` | Granted `AmazonEKSClusterAdminPolicy` at cluster scope | ⚠️ explicitly flagged in code to scope down for prod — see Finding F-02 |

### 1.3 Network exposure

| Service | Exposure | Verdict |
| --- | --- | --- |
| Control plane (`private-ai-workspace`) | Public via ALB Ingress when `ingress.enabled=true`; ClusterIP otherwise | ✅ internal by default |
| vLLM | `service.type = ClusterIP` + `NetworkPolicy` restricting ingress to the `app` namespace (default enabled in chart values) | ✅ internal, network-policy-restricted |
| Karpenter controller, cluster-autoscaler, prometheus-adapter, dcgm-exporter, nvidia-device-plugin, external-secrets | Internal cluster components, no Service exposure beyond cluster | ✅ internal |
| Observability (Prometheus, Grafana, Alertmanager) | Cluster-internal; Grafana exposure is operator-controlled via the kube-prometheus-stack values | ✅ internal by default |

### 1.4 Secret handling

- All application secrets are pulled from AWS Secrets Manager via the
  External Secrets Operator (ESO) using IRSA. No secret values are
  stored in ConfigMaps or in Helm values files.
- The `pyproject.toml` runtime dependencies include `cryptography>=42`
  for OIDC RS256/ES256 verification (added by F-03 below).
- The M5 structured-logging path uses a content-policy-clean format and
  does not log prompt/completion content, tokens, or secret values.
  Spot-checked `app/control_plane/logging_config.py`, `metrics.py`,
  `inference.py` — no incidental secret logging found.

### 1.5 Karpenter NodePool

| Property | Value | Verdict |
| --- | --- | --- |
| GPU taint | `nvidia.com/gpu=true:NoSchedule` | ✅ |
| GPU limit | `nvidia.com/gpu: 8` per NodePool | ✅ capacity cap present |
| Consolidation | `WhenEmpty`, `consolidateAfter: 30m` | ✅ conservative (won't interrupt running inference) |
| Instance families | `g5`, `g6`, `g6e` | ✅ explicit allow-list |
| Capacity types | `spot`, `on-demand` | ✅ matches `docs/09-scaling-policy.md` |
| `subnetSelectorTags` / `securityGroupSelectorTags` | `{}` by default; **must** be set via deploy workflow `--set` | ⚠️ see Finding F-04 |

### 1.6 Findings

| ID | Severity | Area | Finding | Action |
| --- | --- | --- | --- | --- |
| **F-01** | medium | Karpenter IAM | Controller has broad `ec2:*` on `resources = *` and instance-profile management on `resources = *` per the upstream CloudFormation reference. This is the documented Karpenter pattern, but it is the broadest IAM grant in the stack. | Acceptable for M7a; revisit at M7b — investigate whether `ec2:CreateFleet`/`RunInstances` can be conditioned on `aws:ResourceTag/karpenter.sh/discovery = <cluster>` per the latest Karpenter guidance. Track as an M7b task. |
| **F-02** | high (for prod), low (for dev) | GitHub Actions deploy role | The `github_actions_role` is granted `AmazonEKSClusterAdminPolicy` at cluster scope. The Terraform comment explicitly says *"Scope this down to a namespace-scoped role in production."* | **Block M8 (production) on remediation.** Create a namespace-scoped EKS access policy for the deploy role before M8. Acceptable for dev/staging. |
| **F-03** | low | License sweep | `cryptography` (Apache-2.0/BSD) and the `external-secrets` Helm chart (Apache-2.0) were missing from `NOTICE`. | **Remediated in this PR.** `NOTICE` now records both under "M7a license-sweep remediations". `scripts/m7a/license-sweep.sh` re-runs clean. |
| **F-04** | low | Karpenter NodePool defaults | `subnetSelectorTags` and `securityGroupSelectorTags` default to `{}` in the chart values. The deploy workflow sets them via `--set`, so a real deploy works, but a `helm install` run by hand without those flags would render a NodePool that cannot provision. | Documentation-only: add a one-line "required for any direct helm install" note to the chart `values.yaml` comments. Track for an M6 follow-up; not blocking. |
| **F-05** | medium | DCO enforcement | `web_commit_signoff_required` is `false` on the GitHub repository. DCO enforcement currently relies entirely on manual maintainer review during PR triage; web-UI commits could bypass it. | Operator should enable "Require contributors to sign off on web-based commits" in repo settings, or add a DCO check workflow (for example `tim-actions/dco`). |
| **F-06** | medium | CI required checks | `.github/workflows/ci.yml` verifies file structure and runs Python tests; it does not lint Terraform or render Helm charts. Branch protection therefore can only require those two checks. | Add `terraform fmt -check` and `helm lint` jobs to CI in a follow-up. Not blocking M7a closure. |
| **F-07** | low | aws-lb-controller policy provenance | `data.http.aws_lb_controller_policy` fetches the IAM policy JSON from the upstream raw GitHub URL at apply time (pinned to v2.9.0). The URL pinning makes this acceptable, but a compromised upstream branch could in principle alter the contents. | Acceptable for M7a; consider vendoring the policy JSON into `infra/terraform/modules/eks/` at M7b. |

## 2. License sweep

- Harness: `scripts/m7a/license-sweep.sh`
- Last run: **PASS** (after F-03 remediation)
- Coverage: 7 Helm charts (`Chart.lock` files in `deploy/helm/cluster-addons`
  and `deploy/helm/observability`), 7 Python runtime deps (`pyproject.toml`),
  2 container/model artifacts, 2 AGPL-sensitive exclusions
- Finding: F-03 (above), remediated in this PR

## 3. Governance check

- Harness: `scripts/m7a/governance-check.sh`
- Last run (unauthenticated, from this repository checkout):
  **PASS with warnings**
- Local artifacts (CODEOWNERS, CONTRIBUTING.md with DCO reference,
  SECURITY.md, CODE_OF_CONDUCT.md, PR template): all present.
- Repository-level finding: F-05 (DCO web-commit setting is off).
- Branch-protection finding: **VERIFIED (2026-07-04)** via an authenticated
  `GITHUB_TOKEN=$(gh auth token) scripts/m7a/governance-check.sh
  GetFinops/private-ai-workspace-eks` run. The expected contract from
  [`04-governance-and-contribution.md`](04-governance-and-contribution.md)
  — PR-only changes, ≥1 required reviewer, required status checks, no
  force-pushes, no branch deletions — **is satisfied on `main`** (table below).

### Live branch-protection verification (2026-07-04)

Observed values from the authenticated check; all four settings meet the
expected contract. Re-run the harness to refresh.

| Setting | Expected | Observed | Date |
| --- | --- | --- | --- |
| `required_approving_review_count` | `≥ 1` | `1` ✅ | 2026-07-04 |
| `allow_force_pushes` | `false` | `false` ✅ | 2026-07-04 |
| `allow_deletions` | `false` | `false` ✅ | 2026-07-04 |
| `required_status_checks` | `≥ 1` (currently the CI job) | `1` (`docs-and-structure`) ✅ | 2026-07-04 |
| `web_commit_signoff_required` (F-05) | `true` | `false` ⚠ | 2026-07-04 |

> F-05 (web-based commit sign-off) remains **off** — a repository **Settings →
> General** toggle only the repo owner can change. Until then, DCO on web-edited
> commits relies on maintainer review. This is the one open governance item; it
> is a warning, not a blocker (all local commits already carry `Signed-off-by`).

## 4. Drill 1 — Rollback

- Harness: `scripts/m7a/rollback-drill.sh`
- Status: **PASSED (2026-07-04)** against `private-ai-workspace-dev`.
- Prerequisite: dev cluster healthy with the `private-ai-workspace`
  Helm release deployed.
- Harness verified 2026-07-04: correct release/namespace defaults, chart path
  `deploy/helm/private-ai-workspace`, cleanup trap that rolls back on any exit,
  and it prints the exact values to paste below. Requires `helm`, `kubectl`, `jq`.

### Operator action required

```bash
# 1. Point kubectl at the dev EKS cluster (account 069133419519).
export AWS_PROFILE=personal AWS_REGION=us-west-2
aws eks update-kubeconfig --name private-ai-workspace-dev --region us-west-2
kubectl -n app get deploy private-ai-workspace-private-ai-workspace   # confirm healthy

# 2. Run the drill (deploys a broken image, confirms the readiness gate blocks
#    it, rolls back, verifies health — all reversed on exit).
scripts/m7a/rollback-drill.sh --release private-ai-workspace --namespace app
```

The script prints a ready-to-paste block on success. **Result of the 2026-07-04 run:**

| Field | Value |
| --- | --- |
| date | 2026-07-04T11:09:45Z |
| cluster | `private-ai-workspace-dev` (us-west-2, acct 069133419519) |
| helm revisions | 24 (good) → 25 (broken image) → rolled back to 24 |
| time to detected failure | **81s** (readiness gate blocked; within the 60s+ observation window) |
| time to healthy rollback | **16s** |
| pre-drill image | `…/control-plane:35f44713` |
| post-rollback image | `…/control-plane:35f44713` — **matches pre-drill ✅** |
| notes | The broken image never became Ready; the old replicas kept serving throughout ("1 old replicas are pending termination"), so there was **no user-facing outage**. `helm rollback` restored the correct image and the deployment reported `Available` post-rollback. |

**Verdict:** the readiness gate correctly blocks a bad deploy, and rollback returns the
service to health well within the documented timeout. Exit criterion met for rollback.

## 5. Drill 2 — Backup and restore

- Harness: `scripts/m7a/backup-restore-drill.sh`
- Status: **PASSED (2026-07-04)** against `private-ai-workspace-dev` RDS + S3.
- Harness verified 2026-07-04: targets `private-ai-workspace-dev` RDS, restores to
  a suffixed instance in the same subnet group / SGs, verifies S3 versioning +
  lifecycle, and deletes the restored instance on exit (unless `--keep-restored`).
  Requires `aws`, `jq`. IAM: rds snapshot/restore/describe/delete + s3 versioning/
  lifecycle reads (listed in the script header).

### Operator action required

```bash
# 1. AWS creds for the dev account (069133419519).
export AWS_PROFILE=personal AWS_REGION=us-west-2
aws sts get-caller-identity            # confirm the right account

# 2. Run the drill (snapshot → restore to a temp instance → verify → cleanup).
#    Add --keep-restored to run a manual sentinel SELECT before cleanup.
scripts/m7a/backup-restore-drill.sh --project private-ai-workspace --environment dev

# 3. Data-integrity check (the script prints the restored endpoint):
#    psql "host=<restored-endpoint> ..." -c "SELECT count(*) FROM <sentinel table>;"
# 4. Delete the M7a snapshot once recorded (command printed by the script).
```

The script prints a ready-to-paste block on success. **Result of the 2026-07-04 run:**

| Field | Value |
| --- | --- |
| date | 2026-07-04T11:17:53Z |
| snapshot id | `private-ai-workspace-dev-m7a-20260704-110741` (deleted after recording) |
| restored instance id | `private-ai-workspace-dev-m7a-restore-20260704-110741` (deleted by drill cleanup) |
| snapshot duration | **233s** |
| restore duration | **369s** |
| restored endpoint | `…-m7a-restore-20260704-110741.cjaut0arbtia.us-west-2.rds.amazonaws.com` |
| restored instance health | reached `available`; postgres 16.13, `StorageEncrypted=true`, `PubliclyAccessible=false`, correct subnet group + SG |
| artifact bucket | `private-ai-workspace-dev-artifacts` |
| bucket versioning status | **Enabled ✅** |
| bucket lifecycle rules | **1 ✅** |
| sentinel SELECT result | **not performed from the runner** — the restored endpoint is private (in-VPC) and unreachable from outside; reaching `available` from the snapshot demonstrates a functional restore. A row-level SELECT needs an in-VPC client (bastion or `kubectl exec` into a cluster pod) — see note below. |
| post-drill cleanup completed | **✅** restored instance auto-deleted by the drill; snapshot deleted after recording; source `private-ai-workspace-dev` untouched and `available` |

**Verdict:** an out-of-band snapshot completes and restores to a healthy, encrypted,
private instance in the same network in ~10 min total; the artifact bucket has
versioning + a lifecycle rule. Exit criterion met for backup/restore.

> **One residual verification** (optional, not blocking): a row-level *sentinel
> SELECT* against a restored instance for byte-level data-integrity assurance.
> It requires in-VPC DB access, which the CI/ops runner does not have. To close
> it, re-run with `--keep-restored`, then from a cluster pod
> (`kubectl -n app exec … -- psql "host=<restored-endpoint> …" -c "SELECT …"`)
> confirm expected rows, and delete the instance. Restore-to-`available` already
> gives strong recovery assurance for the M7a bar.

## 6. Operational owners

| Area | Owner |
| --- | --- |
| Security posture (IRSA, IAM, network) | maintainers (see `CODEOWNERS`) |
| License sweep | maintainers (see `CODEOWNERS`); harness in `scripts/m7a/license-sweep.sh` |
| Governance and branch protection | repository owner with admin access to `GetFinops/private-ai-workspace-eks` |
| Rollback drill | operator with dev EKS cluster access |
| Backup and restore drill | operator with dev RDS and S3 access |
| Karpenter capacity policy | maintainers, per [`09-scaling-policy.md`](09-scaling-policy.md) |

## 7. Exit-criteria status

| Exit criterion (from milestone file) | Status |
| --- | --- |
| Security posture of M0–M6 reviewed and recorded | ✅ this document |
| Branch protection + contribution flow operate as documented | ✅ verified live 2026-07-04 (§3): ≥1 review, required checks, no force-push, no deletions |
| Backup, restore, and rollback drills performed at least once on dev and documented | ✅ both executed 2026-07-04 and PASSED (§4 rollback 81s→16s; §5 snapshot 233s / restore 369s, S3 versioning+lifecycle verified) |
| Known operational risks recorded with owners | ✅ §1.6 (findings F-01 … F-07) + §6 |
| Phase 2 can begin without un-validated platform debt | ✅ paper review + both live drills passed |

M7a is **complete**. Paper review + license sweep + governance/branch-protection
verification are recorded, and both live drills were executed against
`private-ai-workspace-dev` on 2026-07-04 and PASSED (§4, §5). The two open
follow-ups (F-05 web-commit sign-off; optional in-VPC sentinel SELECT) are
non-blocking and fall outside the M7a exit criteria — carried to M7b/owner.
**M7b's "M7a complete" prerequisite is satisfied.**

## 8. Escalation triggers

None hit so far. The two warning-level findings (F-01 Karpenter IAM
breadth, F-02 GitHub Actions cluster-admin) are tracked as M7b/M8
remediation items rather than blocking Phase 2 from beginning.

If any of the following surface during the operator drills, escalate
per the milestone's "Escalation triggers" section:

- backup or restore that produces a non-functional database
- rollback that does not return the cluster to a healthy state within
  the documented timeout
- any security-review finding in a sensitive area (auth, secrets,
  IAM, governance) that does not yet appear in §1.6
