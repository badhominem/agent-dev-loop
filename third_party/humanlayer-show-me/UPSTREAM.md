# Upstream: show-me

The `show-me` skill in this repo (a future `show-me` skill in this repo) started as a copy of
work by HumanLayer. This file records where it came from and what has changed.

| | |
|---|---|
| Original work | `show-me` skill, v1.0.1 |
| Author | HumanLayer (`support@humanlayer.dev`) |
| Source | https://github.com/humanlayer/skills/blob/main/plugins/show-me/skills/show-me/SKILL.md |
| Copied from commit | `653b6411c1f70c275a18e37673b042ff99f67ceb` (2026-10-08) |
| Copied on | 2026-10-12 |
| License | MIT, Copyright (c) 2026 HumanLayer. Full text in `LICENSE` next to this file |

## What is kept here

- `LICENSE`: the upstream license text, unchanged. Keep it for as long as any
  substantial part of the upstream skill remains in our version.
- `SKILL.upstream.md`: the upstream file exactly as copied. Never edit it.
  It is the baseline for measuring how far our version has moved.

## Changes made in our version

Newest first. One line per change, so the history stays readable.

| Date | Change | Why |
|---|---|---|
| 2026-10-12 | None yet. a future `show-me` skill in this repo is identical to `SKILL.upstream.md` | Starting point |

## Checking how much has changed

```
diff -u third_party/humanlayer-show-me/SKILL.upstream.md skills/show-me/SKILL.md
```

## When to update this file

- Every change to a future `show-me` skill in this repo: add a row above.
- If we pull in newer upstream changes: update the commit and date above, replace
  `SKILL.upstream.md`, and add a row.
- If the skill is ever rewritten so that none of the upstream text or structure
  remains, record that here and keep the credit line anyway:
  "Started from HumanLayer's show-me skill."
