# Cost Estimation

This document provides AWS cost estimates for running `private-ai-workspace-eks`
across environments. It is derived from the infrastructure defined in
`infra/terraform/` and the deployment sizing in `infra/terraform/tfvars/`.

All prices are for **us-east-1 / us-west-2** on-demand rates as of **June 2026**
unless noted. Spot prices vary by availability zone and time; figures below use
recent observed rates. Costs do not include free-tier credits or enterprise
discount agreements.

---

## Critical: EKS Version and Extended Support

Amazon EKS charges **$0.10/hr ($73/month)** per cluster during standard
Kubernetes version support, and **$0.60/hr ($438/month)** during extended
support — a 6× increase.

| EKS version | Standard support ends | Status as of June 2026 |
|-------------|----------------------|------------------------|
| 1.32 | March 23, 2026 | **Extended support — $438/month** |
| 1.33 | July 29, 2026 | Standard support — expires in ~2 months |
| 1.34 | December 2, 2026 | Standard support |
| **1.35** | March 27, 2027 | **Standard support — recommended** |
| 1.36 | August 2, 2027 | Standard support — just released June 2026 |

This repository defaults to **1.35**. Never deploy on 1.32 or 1.33 without a
planned upgrade — the extended support charge applies automatically.

Set a calendar reminder to upgrade the cluster before the standard support end
date of the version you deploy.

---

## Dev Environment

Based on `infra/terraform/tfvars/dev.tfvars.example`.

### Sizing

| Resource | Config |
|----------|--------|
| EKS cluster | k8s 1.35, single cluster |
| CPU node group | 1× m7i.large, ON_DEMAND |
| GPU node group | 0 desired (cold start), max 1× g5.xlarge SPOT |
| RDS | db.t3.medium, Single-AZ, 20 GB gp3 |
| NAT Gateway | 1 (single, dev) |
| Object storage | S3 (minimal dev usage) |

### Monthly Breakdown

| Service | Detail | $/month |
|---------|--------|--------:|
| EKS control plane | $0.10/hr × 730 hr | $73 |
| m7i.large CPU node × 1 | $0.1008/hr × 730 hr, ON_DEMAND | $74 |
| CPU EBS 50 GB gp3 | $0.08/GB-month | $4 |
| RDS db.t3.medium Single-AZ | $0.068/hr × 730 hr | $50 |
| RDS gp3 storage 20 GB | $0.115/GB-month | $2 |
| RDS automated backups | 3-day retention, ~20 GB | $2 |
| NAT Gateway | $0.045/hr × 730 hr + ~10 GB data | $33 |
| ALB (public ingress) | $0.0225/hr base + minimal LCUs | $18 |
| S3 artifacts | ~10 GB storage + requests | $1 |
| ECR | ~1 GB image storage + pulls | $1 |
| Secrets Manager | 2 secrets × $0.40 | $1 |
| Route 53 (optional) | 1 hosted zone | $1 |
| **Total — GPU cold (idle)** | | **$260** |

### Dev GPU Scenarios

GPU nodes start at zero (`gpu_desired_size = 0`) and scale on demand.

| GPU usage pattern | g5.xlarge SPOT rate | GPU add-on | **Monthly total** |
|-------------------|---------------------|-----------|----------------:|
| Off (cold) | — | $0 | **$260** |
| Active 8 hr/day, 22 days | ~$0.60/hr spot | $106 | **$366** |
| Active 24/7 SPOT | ~$0.60/hr spot | $438 | **$698** |
| Active 24/7 ON-DEMAND | $1.006/hr | $735 | **$995** |

*GPU on-demand applies when spot capacity is unavailable.*

### Dev Annual Estimate

| Scenario | $/year |
|----------|-------:|
| GPU cold (development baseline) | **$3,120** |
| GPU active 8 hr/day | **$4,392** |

---

## Production Environment

Based on `infra/terraform/tfvars/prod.tfvars.example`.

### Sizing

| Resource | Config |
|----------|--------|
| EKS cluster | k8s 1.35, single cluster |
| CPU node group | 2× m7i.large, ON_DEMAND (desired); max 6 |
| GPU node group | 1 desired warm, max 4× g5.xlarge SPOT |
| RDS | db.t3.large, Multi-AZ, 20 GB gp3 |
| NAT Gateways | 3 (one per AZ, HA) |
| Object storage | S3 |

### Monthly Breakdown

| Service | Detail | $/month |
|---------|--------|--------:|
| EKS control plane | $0.10/hr × 730 hr | $73 |
| m7i.large CPU node × 2 | $0.1008/hr × 730 hr × 2, ON_DEMAND | $147 |
| CPU EBS 50 GB × 2 gp3 | $0.08/GB-month | $8 |
| g5.xlarge GPU node × 1 | ~$0.51/hr spot × 730 hr (1 warm) | $372 |
| GPU EBS 200 GB gp3 | $0.08/GB-month | $16 |
| RDS db.t3.large Multi-AZ | $0.136/hr × 730 hr × 2 (Multi-AZ) | $199 |
| RDS gp3 storage 20 GB | $0.115/GB-month | $2 |
| RDS automated backups | 14-day retention | $4 |
| NAT Gateway × 3 | $0.045/hr × 730 hr × 3 + ~50 GB data | $100 |
| ALB (public ingress) | $0.0225/hr base + 5–10 LCUs | $40 |
| S3 artifacts | ~100 GB storage + requests | $4 |
| ECR | ~5 GB image storage + pulls | $2 |
| Secrets Manager | 2 secrets × $0.40 | $1 |
| Route 53 | 1 zone + DNS queries | $1 |
| CloudWatch | Container Insights + logs ~10 GB/month | $6 |
| **Total — 1 GPU warm (baseline)** | | **$975** |

### Production GPU Scaling Scenarios

| GPU node count | Capacity | SPOT est. | **Monthly total** |
|----------------|----------|-----------|----------------:|
| 0 (cold) | No inference | $0 | **$603** |
| 1 (warm baseline) | 1× g5.xlarge | $372 | **$975** |
| 2 | 2× g5.xlarge | $744 | **$1,347** |
| 4 (max) | 4× g5.xlarge | $1,488 | **$2,091** |
| 1 (spot unavailable, on-demand) | 1× g5.xlarge OD | $735 | **$1,338** |

### Production Annual Estimate

| Scenario | $/year |
|----------|-------:|
| 0 GPU warm (inference cold) | **$7,236** |
| 1 GPU warm SPOT (baseline) | **$11,700** |
| 2 GPU warm SPOT | **$16,164** |
| 4 GPU warm SPOT (max configured) | **$25,092** |

---

## Savings Opportunities

### Immediate (no commitment required)

| Action | Monthly saving | How |
|--------|----------------|-----|
| Keep EKS on standard support version | **$365** | Use 1.35 (already the default) |
| Zero GPU nodes in dev when not in use | **$372–$738** | Already the default (`desired=0`) |
| S3 VPC Gateway Endpoint | ~$2–$5 | Free endpoint; eliminates S3 NAT charges |
| S3 Gateway Endpoint for ECR layer storage | ~$1–3 | ECR image layers stored in S3; free routing |

### Short-term (after 1 month stable)

| Action | Monthly saving | How |
|--------|----------------|-----|
| m7i.large 1-year Reserved Instance | $25/node (34%) | $48.68 vs $73.58/month/node on-demand |
| RDS db.t3.large 1-year Reserved | ~$33 (33%) | Standard RI, no upfront option |
| Spot GPU commitment (Savings Plan) | ~40% on baseline | EC2 Savings Plan for GPU family |

### Long-term (after usage patterns are known)

| Action | Monthly saving | How |
|--------|----------------|-----|
| m7i.large 3-year Reserved Instance | $44/node (60%) | $29/month all-upfront amortised |
| Right-size RDS to db.t3.medium in prod | $99 | If write load stays low after M3 |
| Upgrade to Graviton RDS (db.t4g.large) | ~15% | Better price/performance for PostgreSQL |
| Multi-region GPU spot pool | Variable | Reduces spot interruption; lower average spot price |

### Combined Savings — Production Baseline (1 GPU warm)

| Optimisation applied | $/month | $/year |
|----------------------|--------:|-------:|
| On-demand baseline | $975 | $11,700 |
| After 1-year RIs (compute + RDS) | ~$840 | ~$10,080 |
| After 3-year RIs + right-sized RDS | ~$695 | ~$8,340 |

---

## Cost Drivers by Priority

```
1. GPU nodes          ████████████████████  $372–$2,091/month (38–84% of prod bill)
2. Multi-AZ NAT       ████                  ~$100/month (10%)
3. Multi-AZ RDS       ████                  ~$199/month (20%)
4. CPU nodes          ███                   ~$147/month (15%)
5. EKS control plane  ██                    $73/month (7%) — $438 if on wrong version
6. ALB + networking   ██                    ~$40/month (4%)
7. Storage + other    █                     ~$20/month (2%)
```

GPU is the dominant variable. All other services form a stable ~$600/month floor.

---

## Services Not Yet Incurring Cost

The following are scaffolded but not yet active. Costs activate when the
corresponding milestone is deployed.

| Service | Milestone | Est. monthly cost |
|---------|-----------|------------------:|
| vLLM inference pods | M4 | included in GPU node cost above |
| Prometheus / Grafana (self-managed) | M5 | $0 additional (runs on CPU nodes) |
| Amazon Managed Prometheus (AMP) | M5 optional | ~$0.90/million samples |
| Amazon Managed Grafana (AMG) | M5 optional | $9/editor seat/month |
| KEDA / Karpenter | M6 | $0 (runs on existing nodes) |
| Additional Secrets Manager secrets | M3+ | $0.40/secret/month each |
| VPC Interface Endpoints | M2 optional | $0.01/hr/AZ ($7.30/month/service/AZ) |

---

## Key Monitoring Recommendations

1. **EKS version upgrade calendar** — set an alert 60 days before the standard
   support end date of your deployed k8s version (1.35 → March 2027).
2. **Spot interruption policy** — define fallback behavior in the vLLM
   Helm chart (`autoscaling`, on-demand fallback) before relying on GPU spot
   nodes in production (M4/M6 task).
3. **NAT Gateway traffic** — add the free S3 VPC Gateway Endpoint at
   `terraform apply` time to eliminate S3 and ECR-layer traffic from NAT
   billing. High ECR pull volume (>200 GB/month) justifies Interface Endpoints.
4. **GPU warm pool** — in dev, `gpu_desired_size = 0` is already the default.
   In production, the 1-node warm pool costs ~$372/month. Scale to 0 in staging
   outside business hours if budget allows.
5. **RDS storage autoscaling** — the `max_allocated_storage_gb = 100` ceiling
   in `modules/rds/variables.tf` prevents unexpected storage cost spikes.

---

## Reference Rates (June 2026, us-east-1)

| Resource | Rate |
|----------|------|
| EKS cluster (standard support) | $0.10/hr |
| EKS cluster (extended support) | $0.60/hr |
| m7i.large EC2 | $0.1008/hr on-demand / ~$0.037/hr spot |
| g5.xlarge EC2 | $1.006/hr on-demand / ~$0.51/hr spot |
| g5.2xlarge EC2 | $1.212/hr on-demand / ~$0.61/hr spot |
| RDS db.t3.medium PostgreSQL | $0.068/hr single-AZ |
| RDS db.t3.large PostgreSQL | $0.136/hr single-AZ |
| RDS gp3 storage | $0.115/GB-month |
| NAT Gateway | $0.045/hr + $0.045/GB processed |
| Internet egress | $0.09/GB (first 10 TB/month) |
| ALB | $0.0225/hr + $0.008/LCU-hr |
| S3 Standard storage | $0.023/GB-month |
| ECR storage | $0.10/GB-month |
| Secrets Manager | $0.40/secret/month |
| gp3 EBS | $0.08/GB-month |
