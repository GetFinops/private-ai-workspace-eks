# Diagram Gallery

All diagrams are maintained as **diagram-as-code** and rendered to PNG so they
render on GitHub without a build step. The source files live in `src/`; the
PNGs are committed alongside them.

- **AWS infrastructure** (the Phase 1 baseline) uses
  [`awsdac`](https://github.com/awslabs/diagram-as-code) for the classic AWS
  reference style: an AWS Cloud frame with VPC, public/private subnets across
  Availability Zones, and official AWS icons.
- **Conceptual AWS diagrams** (Phase 2 feature roadmap, CI/CD) use the
  [`diagrams`](https://diagrams.mingrammer.com/) Python library, which also
  renders non-AWS elements (GitHub, vector store, etc.).
- **Software views** use **UML** authored in PlantUML.

Regenerate everything with:

```bash
scripts/generate-diagrams.sh
```

## AWS Infrastructure (awsdac)

### Phase 1 — Platform Baseline (M0–M8)

Source: [`src/phase1_baseline.yaml`](src/phase1_baseline.yaml)

![Phase 1 platform baseline](phase1_baseline.png)

## Conceptual AWS Diagrams (Python `diagrams`)

### Phase 2 — Proposed Feature Additions (M9+)

Source: [`src/phase2_features.py`](src/phase2_features.py)

![Phase 2 proposed feature additions](phase2_features.png)

### CI/CD and Image Supply Chain (M2)

Source: [`src/cicd_pipeline.py`](src/cicd_pipeline.py)

![CI/CD and image supply chain](cicd_pipeline.png)

## Software Views (UML / PlantUML)

### Control Plane — Component View

Source: [`src/control_plane_components.puml`](src/control_plane_components.puml)

![Control plane component view](control_plane_components.png)

### `POST /v1/chat/completions` — Request Flow

Source: [`src/chat_sequence.puml`](src/chat_sequence.puml)

![Chat request sequence](chat_sequence.png)

## Toolchain

| Output | Tool | Extra prerequisite |
| --- | --- | --- |
| `*.yaml` AWS infra diagrams | [`awsdac`](https://github.com/awslabs/diagram-as-code) | binary auto-downloaded to `.cache/`; network on first run |
| `*.py` conceptual diagrams | [`diagrams`](https://diagrams.mingrammer.com/) (Python) | Graphviz `dot` binary |
| `*.puml` UML diagrams | PlantUML | Java 11+ (jar auto-downloaded to `.cache/`) |

The diagram tooling is development-only and is not a runtime dependency of the
control-plane application.
