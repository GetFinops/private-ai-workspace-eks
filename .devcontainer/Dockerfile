FROM python:3.12-bookworm

# BuildKit auto-populates TARGETARCH (amd64 / arm64) based on the platform.
ARG TARGETARCH

ARG TERRAFORM_VERSION=1.9.8
ARG KUBECTL_VERSION=v1.30.6
ARG HELM_VERSION=v3.16.3

ENV DEBIAN_FRONTEND=noninteractive

# --- base packages ---------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg unzip git bash-completion openssh-client \
      jq less vim sudo \
    && rm -rf /var/lib/apt/lists/*

# --- Terraform -------------------------------------------------------------
RUN curl -fsSL "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_${TARGETARCH}.zip" \
      -o /tmp/tf.zip \
    && unzip /tmp/tf.zip -d /usr/local/bin/ \
    && rm /tmp/tf.zip \
    && terraform version

# --- AWS CLI v2 (arch-aware; uses arm64 on Apple Silicon) ------------------
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) AWS_ARCH=x86_64 ;; \
      arm64) AWS_ARCH=aarch64 ;; \
      *) echo "unsupported arch: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${AWS_ARCH}.zip" -o /tmp/awscliv2.zip; \
    unzip /tmp/awscliv2.zip -d /tmp/; \
    /tmp/aws/install; \
    rm -rf /tmp/awscliv2.zip /tmp/aws; \
    aws --version

# --- kubectl ---------------------------------------------------------------
RUN curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl" \
      -o /usr/local/bin/kubectl \
    && chmod +x /usr/local/bin/kubectl \
    && kubectl version --client

# --- Helm ------------------------------------------------------------------
RUN curl -fsSL "https://get.helm.sh/helm-${HELM_VERSION}-linux-${TARGETARCH}.tar.gz" \
      | tar xz --strip-components=1 -C /usr/local/bin "linux-${TARGETARCH}/helm" \
    && helm version --short

# --- Python dev dependencies (from pyproject.toml) -------------------------
RUN pip install --no-cache-dir "PyJWT>=2.7" "cryptography>=42.0"

# --- non-root user ---------------------------------------------------------
ARG USERNAME=vscode
ARG USER_UID=1000
ARG USER_GID=1000

RUN groupadd --gid ${USER_GID} ${USERNAME} \
    && useradd --uid ${USER_UID} --gid ${USER_GID} -m -s /bin/bash ${USERNAME} \
    && echo "${USERNAME} ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/${USERNAME} \
    && chmod 0440 /etc/sudoers.d/${USERNAME}

USER ${USERNAME}
WORKDIR /workspace
