#!/usr/bin/env bash
# .devcontainer/post-create.sh
# Runs once after container creation:
#   1. Copies read-only host SSH mount into ~/.ssh with correct permissions.
#   2. Seeds known_hosts for common git providers.
#   3. Prints a toolchain version summary.
set -euo pipefail

USER_HOME="${HOME:-/home/vscode}"
HOST_SSH="${USER_HOME}/.ssh-localhost"
SSH_DIR="${USER_HOME}/.ssh"

echo "==> SSH keys"
mkdir -p "${SSH_DIR}"
chmod 700 "${SSH_DIR}"
if [[ -d "${HOST_SSH}" ]]; then
  cp -r "${HOST_SSH}/." "${SSH_DIR}/"
  # private keys 600, public keys + config + known_hosts 644
  find "${SSH_DIR}" -type f                  -exec chmod 600 {} \;
  find "${SSH_DIR}" -type f -name "*.pub"    -exec chmod 644 {} \;
  for f in config known_hosts authorized_keys; do
    [[ -f "${SSH_DIR}/${f}" ]] && chmod 644 "${SSH_DIR}/${f}" || true
  done
  echo "    keys copied from host mount"
else
  echo "    no host ~/.ssh mounted — relying on SSH agent forwarding"
fi

echo "==> known_hosts (github / gitlab / bitbucket)"
touch "${SSH_DIR}/known_hosts" && chmod 644 "${SSH_DIR}/known_hosts"
for host in github.com gitlab.com bitbucket.org; do
  ssh-keygen -F "${host}" -f "${SSH_DIR}/known_hosts" >/dev/null 2>&1 \
    || ssh-keyscan -t rsa,ecdsa,ed25519 "${host}" >> "${SSH_DIR}/known_hosts" 2>/dev/null \
    || true
done

echo "==> AWS credentials"
[[ -d "${USER_HOME}/.aws" ]] \
  && echo "    ~/.aws mounted from host" \
  || echo "    WARNING: ~/.aws not found — run 'aws configure' or mount the host directory"

echo ""
echo "==> Toolchain"
terraform version | head -n1
aws --version
kubectl version --client --output=yaml 2>/dev/null | grep gitVersion | head -n1
helm version --short
python3 --version

echo ""
echo "==> Quick smoke: terraform fmt-check"
terraform -chdir=/workspace/infra/terraform fmt -check -recursive 2>&1 \
  || echo "    (some files not formatted; run: terraform -chdir=infra/terraform fmt -recursive)"

echo ""
echo "Ready. Quick checks:"
echo "  ssh -T git@github.com"
echo "  aws sts get-caller-identity"
echo "  ./scripts/bootstrap-infra.sh --env dev"
