# Internal Inference Contract

The control plane integrates with inference through an internal-only,
OpenAI-compatible chat-completions contract.

## Endpoint Shape

The first supported backend target is vLLM:

```text
POST {INFERENCE_BASE_URL}/v1/chat/completions
```

The Kubernetes service that backs `INFERENCE_BASE_URL` must remain internal to
the cluster or private network. Public ingress belongs at the control plane, not
at model-serving workloads.

## Request Shape

```json
{
  "model": "example-model",
  "messages": [
    {
      "role": "user",
      "content": "hello"
    }
  ],
  "temperature": 0.2,
  "max_tokens": 128
}
```

## Control-Plane Rules

- The control plane must start and serve `/healthz` without GPU capacity.
- `/readyz` reflects external state configuration, not GPU warmness.
- Inference status is exposed as configuration state through
  `/v1/inference/status`.
- Retry, timeout, authentication, and tracing policy should be added before the
  first real network-calling inference client.
