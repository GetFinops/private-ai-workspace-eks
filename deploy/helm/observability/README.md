# Observability Stack Helm Chart

Umbrella chart for the `private-ai-workspace` observability baseline (M5 milestone).

## Components

- **kube-prometheus-stack** — Prometheus, Grafana, Alertmanager, and node exporters
- **vLLM metrics scraping** — collects from inference pods via pod annotations
- **DCGM GPU metrics** — scrapes the DCGM exporter DaemonSet on GPU nodes
- **Alert rules** — inference queue depth, replica availability, control-plane liveness

## Install

```bash
helm dependency update deploy/helm/observability
helm upgrade --install observability deploy/helm/observability \
  --namespace monitoring --create-namespace \
  -f deploy/values/<env>/observability.yaml
```

## What To Set Per Environment

| Key | Description |
|-----|-------------|
| `kubePrometheusStack.grafana.adminPassword` | Grafana admin password (use ExternalSecrets) |
| `kubePrometheusStack.grafana.grafana.ini.server.root_url` | Public Grafana URL |
| `kubePrometheusStack.prometheus.prometheusSpec.retention` | Metric retention window |

## DCGM Exporter

The DCGM exporter is not bundled here. Install it separately via the NVIDIA
GPU Operator or standalone chart. Once running on GPU nodes, the Prometheus
scrape config in this chart will pick it up automatically via pod label
`app.kubernetes.io/name: dcgm-exporter`.

## Grafana Dashboards

Import dashboards from:
- [vLLM dashboard](https://grafana.com/grafana/dashboards/21073) — token throughput, queue depth, request latency
- [DCGM exporter](https://grafana.com/grafana/dashboards/12239) — GPU utilisation, memory, temperature
