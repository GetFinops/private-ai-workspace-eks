"""CI/CD and image supply chain (milestone M2).

Generated with the `diagrams` library (https://diagrams.mingrammer.com/).
Regenerate with: scripts/generate-diagrams.sh

Shows how code reaches the EKS baseline: CI lint/test, image build and push to
ECR, Helm-based deploy, and Terraform-provisioned infrastructure and IRSA.
"""

from __future__ import annotations

import os

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import EKS, ECR
from diagrams.aws.security import IAMRole
from diagrams.k8s.ecosystem import Helm
from diagrams.onprem.ci import GithubActions
from diagrams.onprem.iac import Terraform
from diagrams.onprem.vcs import Github

_OUT = os.path.join(os.path.dirname(__file__), "..", "cicd_pipeline")

_GRAPH_ATTR = {
    "fontsize": "16",
    "labelloc": "t",
    "pad": "0.5",
    "splines": "spline",
}


def main() -> None:
    with Diagram(
        "CI/CD and Image Supply Chain (M2)",
        filename=_OUT,
        outformat="png",
        show=False,
        direction="LR",
        graph_attr=_GRAPH_ATTR,
    ):
        repo = Github("Source repo\n(pull request)")

        with Cluster("GitHub Actions"):
            ci = GithubActions("CI: lint + tests")
            build = GithubActions("build image")
            deploy = GithubActions("deploy (Helm)")

        with Cluster("AWS"):
            ecr = ECR("ECR (images)")
            eks = EKS("EKS cluster")
            irsa = IAMRole("IRSA role")
            tf = Terraform("Terraform\n(VPC/EKS/RDS/S3)")
            helm = Helm("Helm chart")

        repo >> ci >> build
        build >> Edge(label="push") >> ecr
        build >> Edge(label="on success") >> deploy
        deploy >> helm >> eks
        ecr >> Edge(style="dashed", label="pull") >> eks
        tf >> Edge(style="dashed", label="provision") >> eks
        tf >> Edge(style="dashed") >> irsa
        irsa >> Edge(style="dashed", label="pod AWS access") >> eks


if __name__ == "__main__":
    main()
