# Contributing

## Project Model

This repository is open source and maintainer-controlled.

- anyone may open issues and pull requests
- only maintainers may merge
- security-sensitive and architecture-sensitive changes require maintainer review

## Before You Start

- check existing issues and pull requests before opening new work
- propose large architecture changes before implementing them
- avoid introducing AGPL-sensitive optional features without prior review

## Development Expectations

- keep changes focused and easy to review
- include tests or validation notes when practical
- update docs when behavior, deployment, or governance changes
- preserve attribution and provenance for any upstream-derived work

## DCO Sign-off

All commits should include a `Signed-off-by:` trailer.

Example:

```text
Signed-off-by: Your Name <you@example.com>
```

By adding this sign-off, you certify that you have the right to submit the
work under the repository license and that required upstream notices are
preserved where applicable.

## Pull Requests

Pull requests should include:

- a short problem statement
- a summary of the change
- testing or verification notes
- any licensing, provenance, or dependency impact

## What Not To Do

- do not push directly to the protected default branch
- do not vendor third-party code without documenting provenance
- do not merge infra, auth, or secrets changes without review
