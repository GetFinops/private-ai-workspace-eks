"""Phase 2 — proposed product features layered on the baseline (M9+).

Generated with the `diagrams` library (https://diagrams.mingrammer.com/).
Regenerate with: scripts/generate-diagrams.sh

This track is exploratory and maintainer-gated; see
docs/12-phase-2-feature-adoption.md. Left to right: client -> proposed feature
services -> the Phase 1 baseline they build on, with AGPL/excluded components
grouped on the right.
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
    "pad": "0.6",
    "ranksep": "1.0",
    "nodesep": "0.6",
    "splines": "ortho",
}

_BASE = {"color": "#34a853"}
_FEAT = {"color": "#4285f4"}
_DASH_RED = {"style": "dashed", "color": _RED}
_DASH = {"style": "dashed", "color": "#888888"}


def main() -> None:
    with Diagram(
        "Phase 2 - Proposed Feature Additions (M9+)",
        filename=_OUT,
        outformat="png",
        show=False,
        direction="TB",
        graph_attr=_GRAPH_ATTR,
    ):
        client = Client("M9 Web UI / API client")

        with Cluster("Phase 2 feature services (proposed, gated)"):
            agents = Deploy("M11 Agents + tools\n(sandboxed)")
            rag = Deploy("M10 Retrieval / RAG")
            mcp = Deploy("M12 MCP layer")
            media = Deploy("M14 Media (optional)")
            pim = Deploy("M13 PIM (optional)")

        with Cluster("Phase 1 baseline (committed)"):
            api = Deploy("control-plane API")
            vllm = Deploy("vLLM inference")
            pg = RDS("PostgreSQL")
            s3 = S3("S3 artifacts")
            secrets = SecretsManager("Secrets Manager")

        with Cluster("External / excluded by default"):
            vec = RDS("Vector store\n(pgvector / managed)")
            search = Server("Search (AGPL,\nnot vendored)")
            shell = Blank("Shell exec\n(EXCLUDED)")

        # Primary request path
        client >> Edge(**_FEAT) >> api
        api >> Edge(**_FEAT) >> agents

        # Agent orchestration to feature services and inference
        agents >> Edge(**_FEAT) >> rag
        agents >> Edge(**_FEAT) >> mcp
        agents >> Edge(label="internal-only", **_BASE) >> vllm

        # Optional features
        api >> Edge(**_FEAT) >> media
        api >> Edge(**_FEAT) >> pim
        media >> Edge(**_BASE) >> vllm

        # Backend dependencies
        rag >> Edge(**_FEAT) >> vec
        rag >> Edge(**_BASE) >> s3
        pim >> Edge(label="hardened secret + URL", **_DASH) >> secrets

        # External and excluded
        mcp >> Edge(label="opt-in, per-tenant", **_DASH) >> search
        agents >> Edge(label="excluded (multi-tenant)", **_DASH_RED) >> shell


if __name__ == "__main__":
    main()
