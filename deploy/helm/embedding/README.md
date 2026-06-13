# embedding — in-cluster text-embeddings service (M10)

Serves an OpenAI-compatible `POST /v1/embeddings` endpoint using Hugging Face
[Text Embeddings Inference](https://github.com/huggingface/text-embeddings-inference)
(TEI) on CPU. The control plane calls it via `InferenceEmbeddingClient` when
`EMBEDDING_BASE_URL` is set.

- **Model:** `BAAI/bge-small-en-v1.5` (384-dim, MIT) — must match the control
  plane's `EMBEDDING_DIM` (384). The model is downloaded from the HF Hub at pod
  start (not gated; no token); no weights are committed here or baked into the
  image.
- **Endpoint:** `http://<release>.<namespace>.svc.cluster.local/v1/embeddings`.
  With `--namespace inference` and release name `embedding`, that is
  `http://embedding.inference.svc.cluster.local`.

## Deploy

```bash
helm upgrade --install embedding deploy/helm/embedding \
  --namespace inference --create-namespace \
  -f deploy/values/dev/embedding.yaml --wait --timeout 10m
```

Then point the control plane at it (already wired in the Deploy workflow via the
`EMBEDDING_BASE_URL` / `EMBEDDING_MODEL` repo variables):

```bash
--set config.embeddingBaseUrl=http://embedding.inference.svc.cluster.local \
--set config.embeddingModel=BAAI/bge-small-en-v1.5
```

When `EMBEDDING_BASE_URL` is unset the control plane uses the deterministic dev
embedding instead; when it is set but the service is unavailable, retrieval and
memory writes return `503 embedding_unavailable` (graceful degradation).

## Licensing

- TEI image: Apache-2.0.
- `bge-small-en-v1.5`: MIT. Any model swap requires a per-model license review
  (Phase 2 gate); see `NOTICE`.
