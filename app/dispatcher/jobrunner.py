"""Run a single tool as an isolated Kubernetes Job and return the result.

Pure orchestration over a K8sClient (injectable for tests): render the
locked-down manifest, create the Job, poll to completion, read the runner's JSON
result from the pod log, and always reap the Job. Maps Job/pod outcomes to the
same result classes the subprocess sandbox uses (success | unknown_tool |
tool_error | tool_timeout) so callers handle both backends identically.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from app.dispatcher.jobspec import build_job_manifest, job_name
from app.dispatcher.k8sclient import K8sError
from app.dispatcher.jobspec import _safe_label


@dataclass(frozen=True)
class JobConfig:
    image: str
    namespace: str
    runner_service_account: str
    cpu_limit: str = "500m"
    memory_limit: str = "256Mi"
    deadline_seconds: int = 30
    ttl_seconds: int = 120
    workspace_size: str = "64Mi"
    poll_interval: float = 0.5
    overall_timeout: float = 45.0


@dataclass(frozen=True)
class JobOutcome:
    result_class: str           # success | unknown_tool | tool_error | tool_timeout
    result: dict | None = None
    exit_code: int | None = None


def _classify_log(log: str) -> JobOutcome:
    try:
        data = json.loads(log or "{}")
    except (ValueError, json.JSONDecodeError):
        return JobOutcome("tool_error", exit_code=0)
    if not isinstance(data, dict) or not data.get("ok"):
        err = data.get("error") if isinstance(data, dict) else None
        return JobOutcome("unknown_tool" if err == "unknown_tool" else "tool_error", exit_code=0)
    return JobOutcome("success", result=data.get("result"), exit_code=0)


def _job_failed_reason(job: dict) -> str:
    for cond in job.get("status", {}).get("conditions", []) or []:
        if cond.get("type") == "Failed" and cond.get("reason") == "DeadlineExceeded":
            return "tool_timeout"
    return "tool_error"


def run_tool_job(
    *,
    k8s,
    cfg: JobConfig,
    run_id: str,
    tenant_id: str,
    tool: str,
    arguments: dict,
    sleep=time.sleep,
    monotonic=time.monotonic,
) -> JobOutcome:
    manifest = build_job_manifest(
        run_id=run_id,
        tenant_id=tenant_id,
        tool=tool,
        arguments=arguments,
        image=cfg.image,
        namespace=cfg.namespace,
        runner_service_account=cfg.runner_service_account,
        cpu_limit=cfg.cpu_limit,
        memory_limit=cfg.memory_limit,
        deadline_seconds=cfg.deadline_seconds,
        ttl_seconds=cfg.ttl_seconds,
        workspace_size=cfg.workspace_size,
    )
    name = job_name(run_id)
    selector = f"agent-tools/run-id={_safe_label(run_id)}"
    try:
        k8s.create_job(manifest)
    except K8sError:
        return JobOutcome("tool_error")

    try:
        deadline = monotonic() + cfg.overall_timeout
        while True:
            try:
                job = k8s.get_job(name)
            except K8sError:
                return JobOutcome("tool_error")
            status = job.get("status", {})
            if status.get("succeeded"):
                return _read_pod_result(k8s, selector)
            if status.get("failed"):
                reason = _job_failed_reason(job)
                exit_code = _pod_exit_code(k8s, selector)
                return JobOutcome(reason, exit_code=exit_code)
            if monotonic() > deadline:
                return JobOutcome("tool_timeout")
            sleep(cfg.poll_interval)
    finally:
        try:
            k8s.delete_job(name)
        except K8sError:
            pass


def _read_pod_result(k8s, selector: str) -> JobOutcome:
    try:
        pods = k8s.list_pods(selector)
        if not pods:
            return JobOutcome("tool_error")
        pod_name = pods[0].get("metadata", {}).get("name")
        log = k8s.read_pod_log(pod_name)
    except K8sError:
        return JobOutcome("tool_error")
    return _classify_log(log)


def _pod_exit_code(k8s, selector: str) -> int | None:
    try:
        pods = k8s.list_pods(selector)
        if not pods:
            return None
        statuses = pods[0].get("status", {}).get("containerStatuses", []) or []
        term = (statuses[0].get("state", {}) if statuses else {}).get("terminated", {})
        return term.get("exitCode")
    except K8sError:
        return None
