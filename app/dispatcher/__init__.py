"""Tool-runner dispatcher (M11 Job-sandbox).

A small, non-user-facing service that is the ONLY component permitted to create
Kubernetes Jobs for IO-capable agent tools. The internet-facing control plane
holds zero Kubernetes privileges and reaches this dispatcher over HTTP; the
dispatcher owns a fixed, locked-down pod template the control plane cannot
influence. See docs/m11-job-sandbox-design.md and NOTICE ("M11 Job-sandbox
design sign-off").

Stdlib-only, like the control plane. Keep this package free of heavy imports so
it stays auditable.
"""
