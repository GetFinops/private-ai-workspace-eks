"""Phase 2 — proposed product features layered on the baseline (M9+).

Generated with the `diagrams` library (https://diagrams.mingrammer.com/).
Regenerate with: scripts/generate-diagrams.sh

This track is exploratory and maintainer-gated; see
docs/12-phase-2-feature-adoption.md. Red dashed nodes/edges are excluded from
the default build (AGPL-sensitive or non-vendored) and shown only for context.
"""

from __future__ import annotations

import os

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.database import RDS
from diagrams.aws.security import SecretsManager
from diagrams.aws.storage import S3
from diagrams.generic.blank import Blank
from diagrams.k8s.compute import Deploy
from diagrams.onprem.client import Client
from diagrams.onprem.compute import Server

_OUT = os.path.join(os.path.dirname(__file__), "..", "phase2_features")

_RED = "#d93025"

_GRAPH_ATTR = {
    "fontsize": "16",
    "labelloc": "t",
    "pad": "0.5",
    "splines": "spline",
}


def main() -> None:
    with Diagram(
        "Phase 2 - Proposed Feature Additions (M9+)",
        filename=_OUT,
        outformat="png",
        show=False,
        direction="LR",
        graph_attr=_GRAPH_ATTR,
    ):
        with Cluster("Phase 1 baseline (committed)"):
            api = Deploy("control-plane API")
            vllm = Deploy("vLLM inference")
            rds = RDS("PostgreSQL")
            s3 = S3("S3")
            secrets = SecretsManager("Secrets Manager")

        with Cluster("Phase 2 product features (proposed, gated)"):
            gui = Client("M9 Web UI / API client")
            rag = Deploy("M10 Retrieval / RAG")
            agents = Deploy("M11 Agents + tools\n(sandboxed)")
            mcp = Deploy("M12 MCP layer")
            pim = Deploy("M13 PIM integrations\n(optional)")
            media = Deploy("M14 Media services\n(optional)")

        with Cluster("External / excluded by default"):
            vec = RDS("Vector store\n(pgvector / managed)")
            search = Server("External search\n(AGPL, not vendored)")
            shell = Blank("Arbitrary shell exec\n(EXCLUDED)")

        gui >> api
        api >> agents
        agents >> Edge(label="internal-only") >> vllm
        agents >> mcp
        agents >> rag
        rag >> vec
        rag >> s3
        media >> vllm
        pim >> Edge(style="dashed", label="hardened secret + URL layer") >> secrets
        mcp >> Edge(style="dashed", label="opt-in, per-tenant") >> search
        agents >> Edge(style="dashed", color=_RED, label="excluded (multi-tenant)") >> shell


if __name__ == "__main__":
    main()
