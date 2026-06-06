"""CI/CD and image supply chain (milestone M2).

Generated with the `diagrams` library (https://diagrams.mingrammer.com/).
Regenerate with: scripts/generate-diagrams.sh

Two clear lanes: a build-and-deploy pipeline (GitHub Actions -> ECR -> Helm ->
EKS) and an infrastructure-provisioning lane (Terraform -> EKS + IRSA).
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
    "pad": "0.6",
    "ranksep": "1.1",
    "nodesep": "0.8",
    "splines": "ortho",
}

_DASH = {"style": "dashed", "color": "#555555"}


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

        with Cluster("GitHub Actions pipeline"):
            ci = GithubActions("lint + tests")
            build = GithubActions("build image")
            deploy = GithubActions("deploy (Helm)")
            ci >> Edge(label="on success") >> build >> Edge(label="on success") >> deploy

        with Cluster("Runtime (Amazon EKS)"):
            ecr = ECR("ECR (images)")
            helm = Helm("Helm chart")
            eks = EKS("EKS cluster")

        with Cluster("Provisioning"):
            tf = Terraform("Terraform\nVPC/EKS/RDS/S3")
            irsa = IAMRole("IRSA role")

        repo >> ci
        build >> Edge(label="push image") >> ecr
        deploy >> helm >> Edge(label="apply") >> eks
        ecr >> Edge(label="pull", **_DASH) >> eks
        tf >> Edge(label="provision", **_DASH) >> eks
        tf >> Edge(**_DASH) >> irsa >> Edge(label="pod AWS access", **_DASH) >> eks


if __name__ == "__main__":
    main()
