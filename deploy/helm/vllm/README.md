# vLLM Inference Helm Chart

Deploys a vLLM OpenAI-compatible inference service on GPU nodes for the
inference plane of `private-ai-workspace` (M4 milestone).

## Design Constraints

- The Service type is `ClusterIP` — the inference endpoint is internal-only.
- GPU nodes are tainted with `nvidia.com/gpu=true:NoSchedule`; this chart
  includes the required toleration.
- Nodes are selected with `private-ai-workspace/plane: inference`.

## Required Value

| Key | Description |
|-----|-------------|
| `model.id` | Hugging Face model ID (e.g. `meta-llama/Llama-3.1-8B-Instruct`) |

## Internal Endpoint

After install the control plane can reach the inference service at:

```
http://vllm-inference.<namespace>.svc.cluster.local:8000
```

Set `INFERENCE_BASE_URL` in the control-plane ConfigMap to this address.

## GPU Resource Access

If the model is downloaded from Hugging Face Hub, supply the token via a
Kubernetes Secret and configure `extraEnv`:

```yaml
extraEnv:
  - name: HUGGING_FACE_HUB_TOKEN
    valueFrom:
      secretKeyRef:
        name: hf-token
        key: token
```

## Autoscaling (M6)

Set `autoscaling.enabled: true` once kube-prometheus-stack (M5) and a custom
metrics adapter are installed. The HPA scales on the `vllm_num_requests_waiting`
Prometheus metric.
