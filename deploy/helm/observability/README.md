# Observability Stack Helm Chart

Umbrella chart for the `private-ai-workspace` observability baseline (M5 milestone).

## Components

- **kube-prometheus-stack** — Prometheus, Grafana, Alertmanager, and node/kube-state exporters
- **Grafana sidecar dashboards** — three pre-built dashboards auto-loaded from ConfigMaps
- **vLLM metrics scraping** — collects from inference pods via pod annotations
- **DCGM GPU metrics** — scraped from the `dcgm-exporter` DaemonSet deployed by `cluster-addons`
- **Alert rules** — inference queue depth, replica availability, control-plane liveness

## Install

```bash
helm dependency update deploy/helm/observability
helm upgrade --install observability deploy/helm/observability \
  --namespace monitoring --create-namespace \
  -f deploy/values/<env>/observability.yaml
```

## Dashboard delivery

Dashboards are shipped as Helm ConfigMap templates in `templates/dashboards.yaml`.
The Grafana sidecar (`sidecar.dashboards.enabled: true`) watches for ConfigMaps with
`grafana_dashboard: "1"` label across all namespaces and hot-loads them automatically.
No manual Grafana import step is required.

| Dashboard | Source | License | Folder |
|---|---|---|---|
| DCGM GPU Monitoring | aws-samples/sample-genai-on-eks-starter-kit | Apache-2.0 (NVIDIA) | GPU |
| vLLM Performance Statistics | vllm-project/vllm | Apache-2.0 | Inference |
| vLLM Query Statistics | vllm-project/vllm | Apache-2.0 | Inference |

See `NOTICE` for full provenance records.

## DCGM Exporter

The `dcgm-exporter` DaemonSet is installed by the `cluster-addons` umbrella chart (not here).
Once running on GPU nodes, Prometheus scrapes it via the `dcgm-exporter` job configured in
`additionalScrapeConfigs`.

## What to set per environment

| Key | Description |
|-----|-------------|
| `kubePrometheusStack.grafana.adminPassword` | Grafana admin password — use ExternalSecrets in prod |
| `kubePrometheusStack.grafana.grafana\.ini.server.root_url` | Public Grafana URL |
| `kubePrometheusStack.prometheus.prometheusSpec.retention` | Metric retention window |

## Access (port-forward, no Ingress by default)

```bash
# Grafana
kubectl port-forward svc/observability-grafana 3000:80 -n monitoring

# Prometheus
kubectl port-forward svc/observability-kube-prometheus-stack-prometheus 9090:9090 -n monitoring
```

No Ingress is created by this chart. Expose Grafana through the control-plane ingress
or a separate ingress in staging/production (M7).
