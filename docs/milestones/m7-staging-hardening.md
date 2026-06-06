# M7 — Staging Hardening (index)

> This milestone was **split** into two passes as part of the Phase 2
> kickoff. The original single-pass M7 description is preserved in git
> history; the active, current scope is in the two files linked below.

## Why it was split

When Phase 2 was committed, M7's single "make staging look like production"
pass was reorganized so that the platform baseline gets a minimum-hygiene
review **before** Phase 2 features land on top of it, and the full
production-like soak runs **after** Phase 2 against the combined surface.

This means:

- Phase 2 features do not land on an un-validated platform baseline.
- The full staging soak validates the realistic combined topology, not a
  feature-light placeholder.
- The public production release (M8) is gated on the post-Phase-2 soak.

The maintainer decision is recorded in `NOTICE` as the "Phase 2 kickoff and
M7 split" decision record.

## Where the active scope lives

- [M7a — Platform Hardening (minimal, pre–Phase 2)](m7a-platform-hardening-minimal.md)
- [M7b — Full Staging Hardening (post–Phase 2)](m7b-full-staging-hardening.md)

## Sequencing summary

```text
M6 done → M7a → Phase 2 (M9-M14, adoption-gated) → M7b → M8
```

See `docs/10-delivery-roadmap.md` for the full graph, and
`docs/12-phase-2-feature-adoption.md` for Phase 2 governance.
