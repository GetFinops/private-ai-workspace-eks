# Licensing And Policy Review

## Bottom Line

You can publish a **new open-source public repository** inspired by Odysseus and the AWS sample architecture, provided you preserve required notices, keep attribution, avoid branding confusion, and exclude or isolate copyleft-sensitive optional pieces.

## Source Licenses Reviewed

### Odysseus

- Repository license: MIT
- Key source reviewed: [`/tmp/odysseus-review/LICENSE`](/tmp/odysseus-review/LICENSE)

What MIT allows:

- copy
- modify
- publish
- distribute
- sublicense
- sell

Main condition:

- keep the copyright and license notice in copies or substantial portions

### AWS Sample

- Repository license: MIT-0
- Key source reviewed from GitHub: `aws-samples/sample-genai-on-eks`

What MIT-0 means in practice:

- very permissive reuse
- minimal downstream obligations

## Important Attribution Signals In Odysseus

The most important compliance map is:

- [`/tmp/odysseus-review/ACKNOWLEDGMENTS.md`](/tmp/odysseus-review/ACKNOWLEDGMENTS.md)

That file shows Odysseus includes or adapts work from multiple upstream projects, including:

- MIT-licensed sources
- Apache-2.0-licensed sources
- optional or external copyleft-sensitive components

If you copy code from Odysseus, you should preserve all upstream notices relevant to the copied portions, not only the top-level MIT license.

## Safest Redistribution Strategy

Use a **new repo** with selective reuse.

Recommended rules:

- keep your own repo under MIT or Apache-2.0
- preserve Odysseus MIT notices in copied files or substantial copied sections
- preserve Apache-2.0 attribution and notice obligations where applicable
- add a top-level `NOTICE` or `ATTRIBUTION` file listing reused sources
- record provenance per file or per subsystem

## Copyleft-Sensitive Areas To Avoid In Default Distribution

### Optional PyMuPDF feature

Odysseus documents PyMuPDF as optional and AGPL-sensitive for network-served use.

Relevant references:

- [`/tmp/odysseus-review/requirements-optional.txt`](/tmp/odysseus-review/requirements-optional.txt)
- [`/tmp/odysseus-review/src/pdf_forms.py`](/tmp/odysseus-review/src/pdf_forms.py)

Recommendation:

- do not include this feature in the first public build
- if ever added later, isolate it clearly and re-review the licensing impact

### SearXNG

Odysseus acknowledges SearXNG as AGPL-3.0 when run alongside the app.

Recommendation:

- treat it as an external service dependency
- do not vendor or merge its code into your application repo

## Branding And Trademark Caution

Even where copyright permission exists, branding is separate.

Do not present the new project as:

- the official Odysseus project
- an AWS official derivative

Recommended approach:

- new project name
- clear attribution
- clear statement that the project is inspired by, not endorsed by, upstream projects

## Decision

The lowest-risk path is:

1. create a new repository
2. use a permissive top-level license
3. selectively port only needed logic
4. keep attribution and notices
5. avoid AGPL-sensitive optional features in the default build
