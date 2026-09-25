---
name: documentation-update
description: Updates existing documentation pages to reflect code changes, keeping diffs minimal and accurate. Use when affected docs exist and the action is update.
allowed-tools: find_section split_sections extract_frontmatter validate_links
metadata:
  references: 2
  assets: 0
---

# Documentation Update

## Purpose

Modify existing documentation to stay accurate with the codebase.

## Steps

1. Identify the exact sections that changed using `find_section`.
2. Read the current content and the relevant diff/code with registered tools
   (see Repository context below).
3. Apply minimal, surgical edits — do not rewrite untouched sections.
4. Re-validate frontmatter and links afterward.
5. Produce a `DocChangePlan` with the modified files.

## Guidelines

- Small focused diffs are easier to review — prefer them.
- Preserve the original author's voice and structure.
- Never remove working examples without replacing them.
- If the target section no longer exists, treat that part as a create and say
  so explicitly in the plan summary.
- When several pages are affected, produce one plan with multiple files and a
  single commit message — not one plan per page.

## Repository context (important)

- Your callable tools are exactly those listed in your task's toolset list:
  this skill's `allowed-tools`, the run's registered read-only repository
  tools, the PR surface tools the run registers (`get_diff`, `get_files`,
  `affected_docs`) and the draft tools. **Never call a tool that is not
  registered** — the call fails and only wastes your turn budget.
- Do not assume a filesystem. A pull-request run has no local checkout, while an
  evaluation run reads a checkout; both expose their repository reads through
  the read tools named in your toolset list. Decide what exists from that list,
  never from this skill, and read the current page content with it.
- Emit tool calls in **small batches (at most 6 per response)**. A very large
  batch of tool calls in one response gets cut off mid-JSON by the model's
  per-response output cap, and the interrupted calls are discarded.

## Output

A `DocChangePlan` (`repository`, `branch`, `files[{path, content,
action: "update"}]`, `commit_message`, `summary`) limited to the sections
that actually changed.

## References

Read on demand with your file tools — load only when needed:

- `references/change-management.md` — update workflow from detection to validation; follow when planning and executing the update
- `references/backward-compatibility.md` — versioning and deprecation guidance; load when the change affects users on older versions