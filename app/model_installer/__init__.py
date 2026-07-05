"""Model-installer reconciler (design Phase 3) — a separate, tightly-scoped
component that automates model install. NOT part of the control plane; it holds
its own narrow IRSA (scale one GPU nodegroup) + RBAC (patch one vLLM release).
See docs/m11-followups/04-model-management.md and docs/14-user-permissions.md.
"""
