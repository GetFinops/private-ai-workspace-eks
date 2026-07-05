"""model-installer reconciler entrypoint.

Config is env-only (12-factor). Deny-by-default: with no allow-list nothing
installs; with the kill-switch off (INSTALLER_ENABLED=false) the loop idles.
"""
from __future__ import annotations

import logging
import os

from app.model_installer.clients import (
    ControlPlaneClient,
    EksNodegroupClient,
    K8sVllmClient,
)
from app.model_installer.reconciler import InstallerConfig, Reconciler


def _allowlist(raw: str | None) -> frozenset:
    if not raw:
        return frozenset()
    return frozenset(p.strip() for p in raw.split(",") if p.strip())


def main() -> None:  # pragma: no cover - process entrypoint
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format='{"level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
    )
    cp = ControlPlaneClient(
        base_url=os.environ["CONTROL_PLANE_URL"],
        token=os.environ["MODEL_INSTALLER_TOKEN"],
    )
    cfg = InstallerConfig(
        nodegroup=os.environ["GPU_NODEGROUP"],
        cluster=os.environ["EKS_CLUSTER"],
        vllm_namespace=os.environ.get("VLLM_NAMESPACE", "inference"),
        vllm_deployment=os.environ.get("VLLM_DEPLOYMENT", "vllm-inference"),
        vllm_configmap=os.environ.get("VLLM_CONFIGMAP", "vllm-inference-model"),
        allowlist=_allowlist(os.environ.get("INSTALLER_ALLOWLIST")),
        ready_timeout_s=float(os.environ.get("READY_TIMEOUT_S", "900")),
        enabled=os.environ.get("INSTALLER_ENABLED", "true").lower() == "true",
    )
    reconciler = Reconciler(
        cp_client=cp,
        eks_client=EksNodegroupClient(region=os.environ.get("AWS_REGION", "us-west-2")),
        k8s_client=K8sVllmClient(),
        config=cfg,
    )
    reconciler.run_forever(poll_interval_s=float(os.environ.get("POLL_INTERVAL_S", "15")))


if __name__ == "__main__":  # pragma: no cover
    main()
