"""Fixed, locked-down Kubernetes Job template for the tool-runner (M11).

`build_job_manifest` is a pure function (stdlib only) that renders the Job the
dispatcher submits for a single tool call. The template is OWNED BY THE
DISPATCHER and is not influenced by the control plane or the caller — only the
tool name, arguments payload, and identifiers vary. Every isolation control in
docs/m11-job-sandbox-design.md is encoded here, so the test suite asserts on
this manifest directly.

Note on the payload: the {tool, arguments} JSON is passed to the runner via the
AGENT_TOOL_INPUT env var. It is therefore visible in the Job/pod spec to
principals with `get pods` in the jobs namespace — i.e. only the dispatcher SA
and cluster admins — and is ephemeral (the Job is reaped). The runner writes
only the RESULT to stdout, so the pod log (which the dispatcher reads back)
never contains the input. Moving the payload into a per-run mounted Secret is a
documented hardening follow-up.
"""
from __future__ import annotations

import json
import re

# DNS-1123 label safety for names/label values derived from identifiers.
_SAFE = re.compile(r"[^a-z0-9-]+")


def _safe_name(value: str, *, maxlen: int = 40) -> str:
    v = _SAFE.sub("-", value.lower()).strip("-")
    v = v[:maxlen].strip("-")
    return v or "x"


def _safe_label(value: str, *, maxlen: int = 63) -> str:
    # Label values: alphanumerics, '-', '_', '.', must start/end alphanumeric.
    v = re.sub(r"[^a-z0-9A-Z._-]+", "-", value)[:maxlen]
    return v.strip("-._") or "unknown"


def job_name(run_id: str) -> str:
    return "tool-" + _safe_name(run_id)


def build_job_manifest(
    *,
    run_id: str,
    tenant_id: str,
    tool: str,
    arguments: dict,
    image: str,
    namespace: str,
    runner_service_account: str,
    cpu_limit: str = "500m",
    memory_limit: str = "256Mi",
    deadline_seconds: int = 30,
    ttl_seconds: int = 120,
    workspace_size: str = "64Mi",
) -> dict:
    """Render the locked-down Job manifest for one tool call.

    Hard isolation (all fixed here, not caller-controllable):
      - dedicated unprivileged ServiceAccount, NO token mounted, NO IRSA
      - runAsNonRoot, read-only rootfs, drop ALL capabilities, no privilege
        escalation, RuntimeDefault seccomp
      - resource limits, activeDeadlineSeconds, backoffLimit 0, ttl auto-reap
      - scoped emptyDir workspace + tmpfs; no host paths
      - restartPolicy Never; scheduled off GPU (no GPU toleration)
    """
    payload = json.dumps({"tool": tool, "arguments": arguments})
    labels = {
        "app.kubernetes.io/name": "agent-tool-runner",
        "app.kubernetes.io/managed-by": "tool-runner-dispatcher",
        "agent-tools/tenant": _safe_label(tenant_id),
        "agent-tools/run-id": _safe_label(run_id),
        "agent-tools/tool": _safe_label(tool),
    }
    container = {
        "name": "runner",
        "image": image,
        "command": ["python3", "-m", "app.sandbox.runner"],
        "env": [
            {"name": "AGENT_TOOL_INPUT", "value": payload},
            {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"},
            {"name": "PYTHONHASHSEED", "value": "0"},
            # NOTE: do NOT mount a volume over the image's /workspace WORKDIR —
            # the app package lives there. The writable scratch dir is /work.
            {"name": "HOME", "value": "/work"},
            {"name": "TMPDIR", "value": "/tmp"},
        ],
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "runAsNonRoot": True,
            "runAsUser": 1000,
            "capabilities": {"drop": ["ALL"]},
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "resources": {
            "requests": {"cpu": "50m", "memory": "64Mi"},
            "limits": {"cpu": cpu_limit, "memory": memory_limit},
        },
        "volumeMounts": [
            {"name": "workspace", "mountPath": "/work"},
            {"name": "tmp", "mountPath": "/tmp"},
        ],
    }
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name(run_id),
            "namespace": namespace,
            "labels": labels,
        },
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": deadline_seconds,
            "ttlSecondsAfterFinished": ttl_seconds,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "restartPolicy": "Never",
                    "serviceAccountName": runner_service_account,
                    "automountServiceAccountToken": False,
                    "nodeSelector": {"kubernetes.io/os": "linux"},
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 1000,
                        "fsGroup": 1000,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [container],
                    "volumes": [
                        {"name": "workspace", "emptyDir": {"sizeLimit": workspace_size}},
                        {"name": "tmp", "emptyDir": {"sizeLimit": "16Mi"}},
                    ],
                },
            },
        },
    }
