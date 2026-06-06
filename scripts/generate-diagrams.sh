#!/usr/bin/env bash
#
# Regenerate the project diagrams from their diagram-as-code sources.
#
#   AWS architecture : Python `diagrams` library + Graphviz  (docs/diagrams/src/*.py)
#   Software (UML)    : PlantUML + Java                       (docs/diagrams/src/*.puml)
#
# Prerequisites:
#   - Graphviz (provides the `dot` binary):  apt-get install -y graphviz
#   - Python package:                         pip install diagrams
#   - Java (for PlantUML):                    any JRE/JDK 11+
#   - PlantUML jar: auto-downloaded to .cache/ if PLANTUML_JAR is not set.
#
# Usage:
#   scripts/generate-diagrams.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/docs/diagrams/src"
OUT="$ROOT/docs/diagrams"
PLANTUML_VERSION="1.2024.7"

echo "==> AWS architecture diagrams (Python diagrams + Graphviz)"
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
