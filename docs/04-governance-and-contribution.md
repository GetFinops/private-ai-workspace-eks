# Governance And Contribution

## Project Posture

The project should be open source, but **maintainer-controlled**.

That means:

- anyone may read the code and submit pull requests
- only maintainers can merge
- sensitive areas always require maintainer review

## Recommended Repository Controls

### Branch protection

Protect the default branch with:

- pull-request-only changes
- at least one required maintainer review
- required status checks
- no force-pushes
- no direct pushes except for trusted maintainers if absolutely necessary

### CODEOWNERS

Use `CODEOWNERS` to require maintainer approval for:

- Kubernetes manifests
- Terraform or cluster provisioning
- auth and session code
- secrets handling
- release automation
- security-sensitive integrations

## Contributor Legal Model

Use **DCO sign-off** as the default inbound contribution rule.

Why DCO is the best starting choice:

- simpler than a CLA
- common in open-source infrastructure projects
- enough for a permissive-license project in most cases
- lower friction for external contributors

Recommended policy:

- every commit must include `Signed-off-by:`
- contributors must confirm they have the right to submit the code
- contributors must preserve upstream notices when adapting third-party code

## Community Policy Files

Ship these files from the first public commit:

- `CONTRIBUTING.md`
- `CODE_OF_CONDUCT.md`
- `SECURITY.md`
- `NOTICE` or `ATTRIBUTION`

## Suggested Maintainer Rules

- architecture changes need maintainer approval before large implementation work
- security-sensitive fixes may be handled privately before public disclosure
- new third-party dependencies should be reviewed for license compatibility before merge
- copied upstream files must be tagged in provenance or attribution tracking

## Contribution Access Level

The chosen operating model is:

- public visibility
- restricted merge authority
- controlled review for critical code paths

This gives you open-source transparency without losing project direction or security discipline.
