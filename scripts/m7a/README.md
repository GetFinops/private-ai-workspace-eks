# M7a Drill and Sweep Harnesses

Operator-runnable scripts that back the M7a milestone defined in
[`../../docs/milestones/m7a-platform-hardening-minimal.md`](../../docs/milestones/m7a-platform-hardening-minimal.md).

The findings, exit-criteria status, and per-drill record sheets live in
[`../../docs/m7a-report.md`](../../docs/m7a-report.md). The scripts
here are stateless; the report is the source of truth.

## Scripts

| Script | Requires | Mutates AWS or cluster? | Purpose |
| --- | --- | --- | --- |
| `license-sweep.sh` | `bash`, `grep` | no | Verifies every Helm chart in `Chart.lock` files and every runtime Python dependency in `pyproject.toml` has provenance in `NOTICE`; also asserts AGPL-sensitive components (PyMuPDF, SearXNG) are absent from the default build. |
| `governance-check.sh` | `curl`, `jq`; optional `GITHUB_TOKEN` for branch-protection read | no | Verifies local governance artifacts (CODEOWNERS, CONTRIBUTING.md, SECURITY.md, PR template) and queries the GitHub API for repository + branch-protection settings against the contract in `docs/04-governance-and-contribution.md`. |
| `rollback-drill.sh` | `helm`, `kubectl`; live dev EKS cluster context | yes — deploys a deliberately broken release then rolls it back via `helm rollback` | Exercises the readiness gate by deploying a bogus image, confirms the rollout times out, then rolls back to the previous revision and verifies post-rollback health. |
| `backup-restore-drill.sh` | `aws`, `jq`; AWS credentials with RDS + S3 read; optional `--keep-restored` to leave the restored instance for manual connectivity testing | yes — creates an RDS snapshot, restores it to a new instance with `-m7a-restore-<ts>` suffix, and (by default) deletes the restored instance at the end | Takes an out-of-band RDS snapshot, restores to a new instance, verifies it reaches `available`, and reports S3 versioning + lifecycle status for the project artifact bucket. |

## Usage

All four scripts are safe to run repeatedly. The two live drills
(`rollback-drill.sh` and `backup-restore-drill.sh`) require a real dev
cluster and AWS credentials; the other two run from a fresh checkout
with no infrastructure.

```bash
scripts/m7a/license-sweep.sh
GITHUB_TOKEN="$GH_TOKEN" scripts/m7a/governance-check.sh
scripts/m7a/rollback-drill.sh --release private-ai-workspace --namespace app
scripts/m7a/backup-restore-drill.sh --project private-ai-workspace --environment dev
```

Each script prints a "Record the following in docs/m7a-report.md"
block on success with the fields that need to land in the report.

## What is intentionally out of scope

- Continuous-deployment changes (those belong in `.github/workflows/`)
- Anything that destroys data without explicit operator confirmation
  (the rollback drill rolls back to the previous revision; the
  backup-and-restore drill skips final snapshots only for the
  ephemeral restored copy, never for the source instance)
- Phase 2 surface — that is M7b's scope, not M7a's
