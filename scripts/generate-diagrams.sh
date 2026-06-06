#!/usr/bin/env bash
#
# Regenerate the project diagrams from their diagram-as-code sources.
#
#   AWS architecture : Python `diagrams` library + Graphviz  (docs/diagrams/src/*.py)
#   Software (UML)    : PlantUML + Java                       (docs/diagrams/src/*.puml)
#
# Prerequisites:
#   - awsdac (awslabs/diagram-as-code): auto-downloaded to .cache/ for Linux/macOS.
#   - Graphviz (provides the `dot` binary):  apt-get install -y graphviz
#   - Python package:                         pip install diagrams
#   - Java (for PlantUML):                    any JRE/JDK 11+
#   - PlantUML jar: auto-downloaded to .cache/ if PLANTUML_JAR is not set.
#   - awsdac and PlantUML downloads require network access on first run.
#
# Usage:
#   scripts/generate-diagrams.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/docs/diagrams/src"
OUT="$ROOT/docs/diagrams"
PLANTUML_VERSION="1.2024.7"
AWSDAC_VERSION="0.23"

echo "==> AWS infrastructure diagrams (awsdac / awslabs diagram-as-code)"
AWSDAC_BIN="${AWSDAC:-$ROOT/.cache/awsdac/awsdac}"
if [ ! -x "$AWSDAC_BIN" ]; then
  case "$(uname -s)-$(uname -m)" in
    Linux-x86_64)  AWSDAC_PKG="awsdac-v${AWSDAC_VERSION}_linux-amd64.zip" ;;
    Linux-aarch64) AWSDAC_PKG="awsdac-v${AWSDAC_VERSION}_linux-arm64.zip" ;;
    Darwin-x86_64) AWSDAC_PKG="awsdac-v${AWSDAC_VERSION}_darwin-amd64.zip" ;;
    Darwin-arm64)  AWSDAC_PKG="awsdac-v${AWSDAC_VERSION}_darwin-arm64.zip" ;;
    *) echo "ERROR: unsupported platform for awsdac auto-download; set AWSDAC to a binary." >&2; exit 1 ;;
  esac
  echo "    downloading awsdac v${AWSDAC_VERSION}"
  mkdir -p "$ROOT/.cache/awsdac"
  curl -fsSL -o "$ROOT/.cache/awsdac/awsdac.zip" \
    "https://github.com/awslabs/diagram-as-code/releases/download/v${AWSDAC_VERSION}/${AWSDAC_PKG}"
  ( cd "$ROOT/.cache/awsdac" && unzip -o -q awsdac.zip )
  AWSDAC_REAL="$(find "$ROOT/.cache/awsdac" -type f -name awsdac | head -1)"
  cp "$AWSDAC_REAL" "$AWSDAC_BIN"
  chmod +x "$AWSDAC_BIN"
fi
for yaml in "$SRC"/*.yaml; do
  [ -e "$yaml" ] || continue
  name="$(basename "${yaml%.yaml}")"
  echo "    rendering $(basename "$yaml")"
  "$AWSDAC_BIN" "$yaml" -o "$OUT/${name}.png" -f
done

echo "==> Conceptual AWS diagrams (Python diagrams + Graphviz)"
if ! command -v dot >/dev/null 2>&1; then
  echo "ERROR: Graphviz 'dot' not found. Install graphviz (e.g. apt-get install -y graphviz)." >&2
  exit 1
fi
if ! python3 -c "import diagrams" >/dev/null 2>&1; then
  echo "ERROR: Python 'diagrams' package not found. Install it (e.g. pip install diagrams)." >&2
  exit 1
fi
for py in "$SRC"/*.py; do
  echo "    rendering $(basename "$py")"
  ( cd "$SRC" && python3 "$py" )
done

echo "==> Software UML diagrams (PlantUML + Java)"
if ! command -v java >/dev/null 2>&1; then
  echo "ERROR: Java not found. Install a JRE/JDK 11+ to render PlantUML diagrams." >&2
  exit 1
fi
PLANTUML_JAR="${PLANTUML_JAR:-$ROOT/.cache/plantuml.jar}"
if [ ! -f "$PLANTUML_JAR" ]; then
  echo "    downloading plantuml.jar ($PLANTUML_VERSION)"
  mkdir -p "$(dirname "$PLANTUML_JAR")"
  curl -fsSL -o "$PLANTUML_JAR" \
    "https://github.com/plantuml/plantuml/releases/download/v${PLANTUML_VERSION}/plantuml-${PLANTUML_VERSION}.jar"
fi
java -jar "$PLANTUML_JAR" -tpng -o "$OUT" "$SRC"/*.puml

echo "==> Done. PNGs written to docs/diagrams/"
