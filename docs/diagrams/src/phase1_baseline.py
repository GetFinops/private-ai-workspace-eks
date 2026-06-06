"""Phase 1 — platform baseline AWS architecture (milestones M0-M8).

Generated with the `diagrams` library (https://diagrams.mingrammer.com/).
Regenerate with: scripts/generate-diagrams.sh

This renders the committed two-plane EKS baseline: a CPU control plane and an
isolated, internal-only GPU inference plane, backed by managed AWS data and
identity services.
"""

from __future__ import annotations

import os

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import EKS, ECR
from diagrams.aws.database import RDS
from diagrams.aws.management import Cloudwatch
from diagrams.aws.network import Route53
from diagrams.aws.network import ElbApplicationLoadBalancer as ALB
from diagrams.aws.security import ACM, Cognito, SecretsManager
from diagrams.aws.storage import S3
from diagrams.k8s.compute import Deploy
from diagrams.onprem.client import Users
from diagrams.onprem.monitoring import Grafana, Prometheus

_OUT = os.path.join(os.path.dirname(__file__), "..", "phase1_baseline")

_GRAPH_ATTR = {
    "fontsize": "16",
    "labelloc": "t",
    "pad": "0.5",
    "splines": "spline",
}


def main() -> None:
    with Diagram(
        "Phase 1 - Platform Baseline (M0-M8)",
        filename=_OUT,
        outformat="png",
        show=False,
        direction="LR",
        graph_attr=_GRAPH_ATTR,
    ):
        users = Users("Users / API clients")
        dns = Route53("Route 53")
        tls = ACM("ACM (TLS)")
        oidc = Cognito("OIDC issuer\n(Cognito / Okta)")

        with Cluster("VPC"):
            with Cluster("Public subnets"):
                alb = ALB("Application\nLoad Balancer")

            with Cluster("Private subnets"):
                with Cluster("EKS cluster"):
                    with Cluster("Control plane (CPU nodes)"):
                        app = Deploy("control-plane API\n/healthz /readyz /chat")
                    with Cluster("Inference plane (GPU nodes)"):
                        vllm = Deploy("vLLM (internal-only)")

        with Cluster("Managed AWS services"):
            rds = RDS("RDS PostgreSQL")
            s3 = S3("S3 (artifacts)")
            secrets = SecretsManager("Secrets Manager")
            ecr = ECR("ECR (images)")

        with Cluster("Observability"):
            prom = Prometheus("Prometheus / AMP")
            graf = Grafana("Grafana / AMG")
            cw = Cloudwatch("CloudWatch logs")

        users >> dns >> alb
        tls >> Edge(style="dashed", label="TLS") >> alb
        alb >> Edge(label="HTTPS") >> app
        app >> Edge(label="internal-only") >> vllm
        app >> rds
        app >> s3
        app >> Edge(style="dashed", label="verify token") >> oidc
        secrets >> Edge(style="dashed", label="External Secrets sync") >> app
        ecr >> Edge(style="dashed", label="images") >> app
        ecr >> Edge(style="dashed") >> vllm
        app >> prom
        vllm >> prom
        prom >> graf
        app >> Edge(style="dashed") >> cw


if __name__ == "__main__":
    main()
