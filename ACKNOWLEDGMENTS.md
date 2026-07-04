# Acknowledgments

`private-ai-workspace-eks` stands on the shoulders of a large open-source
community. This page is a human-readable thank-you to the projects, standards,
and people whose work makes this one possible.

> This is a courtesy credits page. The authoritative, legal provenance and
> license record — every adapted pattern and vendored artifact, with its license
> and modifications — lives in [`NOTICE`](NOTICE). Where the two differ, `NOTICE`
> governs.

## Upstream inspiration

- **Odysseus** (`pewdiepie-archdaemon/odysseus`) — the MIT-licensed v1.0 snapshot
  (commit `e5c99a5`) inspired several control-plane and UI patterns and the
  design-token palette. This project is an independent, clean-room reimagining,
  **not** the official Odysseus project and not endorsed by its maintainers.
  Upstream has since relicensed to AGPL-3.0; see [`NOTICE`](NOTICE) for the exact
  provenance and licensing posture.

## Inference & machine learning

- **vLLM** — the OpenAI-compatible model-serving engine on the GPU inference plane.
- **Mistral AI** — the default open-weight chat model (Mistral-7B-Instruct).
- **OpenAI Whisper**, **faster-whisper**, and **CTranslate2** — speech-to-text.
- **Stability AI — Stable Diffusion XL** — image generation.
- **Hugging Face — Text Embeddings Inference** — retrieval embeddings.
- **Mozilla — pdf.js** — client-side PDF text extraction for RAG upload.
- **Hugging Face Hub** — model distribution.

## Platform & infrastructure

- **Kubernetes**, **Helm**, and the **CNCF** ecosystem.
- **Amazon Web Services** — EKS, RDS (PostgreSQL), S3, Secrets Manager, ECR, IRSA.
- **Karpenter** and the **Kubernetes Cluster Autoscaler** — elastic GPU capacity.
- **External Secrets Operator** — secret delivery from AWS Secrets Manager.
- **NVIDIA device plugin** and **DCGM exporter** — GPU scheduling and metrics.
- **Prometheus**, **Grafana**, and the **kube-prometheus-stack** — observability.
- **OpenTelemetry** — tracing.
- **external-dns** — DNS management.
- **pgvector** — vector similarity for retrieval and memory.
- **Terraform** — infrastructure as code.

## Language, libraries & tooling

- **Python** and its standard library — the control plane is stdlib-first.
- **PyJWT** (OIDC token verification), **psycopg** (PostgreSQL), **boto3** (AWS),
  **prometheus-client**, **cryptography**.
- **Fira Code** (Nikita Prokopov and contributors) — the monospace UI/code font.

## Standards & protocols

- **OpenID Connect (OIDC)** — authentication.
- **Model Context Protocol (MCP)** — sandboxed tool integration.
- **The OpenAI-compatible API shape** — the lingua franca this project speaks to
  its inference, embedding, and media backends.

## And

Everyone who builds, maintains, and documents the open-source software above —
thank you. Full license texts and attributions are preserved in [`NOTICE`](NOTICE).
