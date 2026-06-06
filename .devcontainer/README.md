# Local Dev Container

A reproducible local environment for working on this repo's control plane and
the `infra/terraform` EKS stack.

## What's inside

- **Python 3.12** (base image) — matches `app/Dockerfile` / `pyproject.toml` (`>=3.11`)
- **Terraform** + `tflint` + `tfsec` — satisfies `versions.tf` (`>= 1.6.0`)
- **AWS CLI v2** — used by Terraform's EKS `exec` auth and `scripts/bootstrap-infra.sh`
- **kubectl** + **Helm** — for the `deploy/helm` charts and cluster access

## Credential mapping (host → container)

Credentials are **mapped from your host**, never baked into the image or committed:

| Host | Container | Mode |
|------|-----------|------|
| `~/.ssh` | copied into `~/.ssh` (via `~/.ssh-localhost`) | read-only mount, perms fixed on create |
| `~/.aws` | `~/.aws` | bind mount (read-write, so SSO/credential cache works) |

SSH agent forwarding from the host also works automatically if your agent is running.

## Usage

1. Install Docker + the VS Code / Cursor **Dev Containers** extension.
2. Optionally export `AWS_REGION` / `AWS_PROFILE` on the host before opening so they
   propagate into the container.
3. Open the repo and **Reopen in Container**.
4. After build, verify:

   ```bash
   ssh -T git@github.com         # git SSH works
   aws sts get-caller-identity   # AWS creds resolve
   terraform -chdir=infra/terraform version
   ./scripts/bootstrap-infra.sh  # init / validate / plan (dev)
   ```

## Notes

- **Windows hosts:** change `${localEnv:HOME}` to `${localEnv:USERPROFILE}` in
  `devcontainer.json` mounts.
- The `~/.aws` mount is read-write only so `aws sso login` and the CLI credential
  cache can persist; the directory still lives on your host. No secrets are stored
  in the repo or image.
