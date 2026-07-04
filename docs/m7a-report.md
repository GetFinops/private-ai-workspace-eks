# M7a — Platform Hardening (minimal, pre–Phase 2) — Report

> Status of the milestone defined in
> [`milestones/m7a-platform-hardening-minimal.md`](milestones/m7a-platform-hardening-minimal.md).
>
> This is the public record of the M7a pass. Sensitive operational
> details (specific AWS account ids, secret values, ARNs) are kept out
> of this document and are recorded in the maintainer's private
> deployment log instead.

## Status

Partial — paper review, license sweep, and governance/branch-protection
verification are complete (the latter re-verified live against the GitHub API
on 2026-07-04, §3). The only remaining items are the two infrastructure drills
(rollback, backup-and-restore), which require dev EKS + RDS/S3 access; their
harnesses are verified and the operator run-books are turnkey below (§4, §5).

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
- Status: **pending operator execution against the dev EKS cluster.**
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

The script prints a ready-to-paste block on success; copy it into the table:

| Field | Value |
| --- | --- |
| date | _to fill_ |
| cluster | _to fill_ |
| time to detected failure | _to fill_ |
| time to healthy rollback | _to fill_ |
| pre-drill image | _to fill_ |
| post-rollback image | _to fill_ |
| notes | _to fill_ |

## 5. Drill 2 — Backup and restore

- Harness: `scripts/m7a/backup-restore-drill.sh`
- Status: **pending operator execution against the dev RDS instance and S3 bucket.**
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

The script prints a ready-to-paste block on success; copy it into the table:

| Field | Value |
| --- | --- |
| date | _to fill_ |
| snapshot id | _to fill_ |
| restored instance id | _to fill_ |
| snapshot duration | _to fill_ |
| restore duration | _to fill_ |
| restored endpoint | _to fill_ |
| artifact bucket | _to fill_ |
| bucket versioning status | _to fill_ |
| bucket lifecycle rules | _to fill_ |
| sentinel SELECT result | _to fill_ |
| post-drill cleanup completed | _to fill_ |

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
| Backup, restore, and rollback drills performed at least once on dev and documented | ⏳ harnesses verified + run-books turnkey; awaiting operator runs (§4, §5) |
| Known operational risks recorded with owners | ✅ §1.6 (findings F-01 … F-07) + §6 |
| Phase 2 can begin without un-validated platform debt | ✅ for paper review; conditional on §4 + §5 drill runs |

M7a is **partial**. The paper-review portion is complete and the
license sweep is clean. The two live drills are scripted and ready;
the operator runs them and updates §4 + §5 before the milestone can be
declared closed.

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
