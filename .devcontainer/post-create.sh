#!/usr/bin/env bash
# .devcontainer/post-create.sh
#
# Runs once after the dev container is created. It:
#   1. Copies the read-only host SSH keys into ~/.ssh with correct perms.
#   2. Seeds known_hosts for common git providers.
#   3. Verifies the toolchain (terraform, aws, kubectl, helm, python).
#
# Secrets are never written into the image or the repo; keys come from the
# host mount and stay in the container's user home only.

set -euo pipefail

USER_HOME="${HOME:-/home/vscode}"
HOST_SSH="${USER_HOME}/.ssh-localhost"
SSH_DIR="${USER_HOME}/.ssh"

echo "==> Wiring SSH keys"
if [[ -d "${HOST_SSH}" ]]; then
  mkdir -p "${SSH_DIR}"
  chmod 700 "${SSH_DIR}"
  # Copy contents (private keys, public keys, config) from the read-only mount.
  cp -r "${HOST_SSH}/." "${SSH_DIR}/" 2>/dev/null || true
  # Lock down perms: private keys 600, public/config 644.
  find "${SSH_DIR}" -type f -exec chmod 600 {} \;
  find "${SSH_DIR}" -type f -name '*.pub' -exec chmod 644 {} \;
  [[ -f "${SSH_DIR}/config" ]] && chmod 644 "${SSH_DIR}/config" || true
  [[ -f "${SSH_DIR}/known_hosts" ]] && chmod 644 "${SSH_DIR}/known_hosts" || true
  echo "    copied keys from host mount"
else
  echo "    no host ~/.ssh mounted; relying on SSH agent forwarding (if any)"
  mkdir -p "${SSH_DIR}"
  chmod 700 "${SSH_DIR}"
fi

echo "==> Seeding known_hosts for git providers"
touch "${SSH_DIR}/known_hosts"
chmod 644 "${SSH_DIR}/known_hosts"
for host in github.com gitlab.com bitbucket.org; do
  if ! ssh-keygen -F "${host}" -f "${SSH_DIR}/known_hosts" >/dev/null 2>&1; then
    ssh-keyscan -t rsa,ecdsa,ed25519 "${host}" >> "${SSH_DIR}/known_hosts" 2>/dev/null || true
  fi
done

echo "==> AWS credentials"
if [[ -d "${USER_HOME}/.aws" ]]; then
  echo "    ~/.aws mounted from host"
else
  echo "    WARNING: ~/.aws not mounted; set AWS_PROFILE or run 'aws configure'"
fi

echo ""
echo "==> Toolchain versions"
command -v terraform >/dev/null && terraform version | head -n 1 || echo "terraform: MISSING"
command -v aws       >/dev/null && aws --version                 || echo "aws: MISSING"
command -v kubectl   >/dev/null && kubectl version --client --output=yaml 2>/dev/null | grep -m1 gitVersion || echo "kubectl: MISSING"
command -v helm      >/dev/null && helm version --short          || echo "helm: MISSING"
python3 --version

echo ""
echo "==> Sanity: terraform fmt check (infra/terraform)"
if [[ -d infra/terraform ]]; then
  terraform -chdir=infra/terraform fmt -check -recursive || \
    echo "    (terraform files not formatted; run 'terraform -chdir=infra/terraform fmt -recursive')"
fi

echo ""
echo "Dev container ready. Try:"
echo "  ssh -T git@github.com           # verify git SSH"
echo "  aws sts get-caller-identity     # verify AWS creds"
echo "  ./scripts/bootstrap-infra.sh    # terraform init/validate/plan"
