"""Sandbox-safe tools: pure functions over JSON arguments, stdlib only.

This module MUST NOT import the control plane, config, database, AWS SDK, or do
any network or file I/O. It is imported by the out-of-process sandbox runner
(app/sandbox/runner.py) and — for schema/metadata only — by the control-plane
tool framework. Keeping it dependency-free and side-effect-free is what makes
the sandbox a sandbox: a tool can only compute over its arguments.

Adding a tool that needs network egress, filesystem writes, or credentials is
an escalation trigger (docs/m11-sandbox-design.md) and must not be added here.
"""
from __future__ import annotations

# Per-tool input ceiling — also enforced control-plane-side before spawn.
_MAX_TEXT = 100_000


def _text_stats(arguments: dict) -> dict:
    """Return character, word, and line counts for a text string."""
    text = arguments.get("text")
    if not isinstance(text, str):
        raise ValueError("'text' must be a string")
    if len(text) > _MAX_TEXT:
        raise ValueError("'text' too large")
    words = text.split()
    return {
        "characters": len(text),
        "words": len(words),
        "lines": text.count("\n") + (1 if text else 0),
    }


# name -> spec. `schema` is the minimal declarative argument contract the
# control plane validates before spawning; `run` is the pure executor the
# runner invokes inside the sandbox.
#   `executor` — which sandbox backend runs the tool:
#     "subprocess" (default) — out-of-process child of the control plane; correct
#       for pure compute;
#     "job" — a per-call Kubernetes Job via the tool-runner dispatcher (M11
#       Job-sandbox); stronger isolation for tools needing a filesystem
#       workspace or allow-listed egress. Both run this same pure registry.
TOOLS: dict[str, dict] = {
    "text_stats": {
        "description": "Return character, word, and line counts for a text string.",
        "schema": {
            "text": {"type": "string", "required": True, "max_len": _MAX_TEXT},
        },
        "run": _text_stats,
        "executor": "subprocess",
    },
    # Pure-compute demonstrator for the Job-sandbox path: identical logic to
    # text_stats but routed through a Kubernetes Job, so the isolation mechanics
    # (no creds, default-deny network, ephemeral FS, reaping) can be validated
    # without introducing a real IO/egress tool.
    "text_stats_job": {
        "description": "Like text_stats, but executed in an isolated Kubernetes Job.",
        "schema": {
            "text": {"type": "string", "required": True, "max_len": _MAX_TEXT},
        },
        "run": _text_stats,
        "executor": "job",
    },
}


def tool_names() -> list[str]:
    return sorted(TOOLS.keys())


def tool_executor(name: str) -> str:
    """Return the executor backend for a tool ("subprocess" | "job")."""
    return TOOLS.get(name, {}).get("executor", "subprocess")
