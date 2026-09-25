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
5. Stream the assigned page through `start_draft`, `append_chunk`, and
   `finalize_draft`; then produce a `DocChangePlan` describing that page.

## Guidelines

- Small focused diffs are easier to review — prefer them.
- Preserve the original author's voice and structure.
- Never remove working examples without replacing them.
- If the target section no longer exists, treat that part as a create and say
  so explicitly in the plan summary.
- If the target page cannot be read at all (not found / 404), stop after **two
  attempts** and treat it as a new page: `start_draft` with `action="create"`
  for the same path, then write the content the task requires. Do not retry the
  read, do not guess the file's previous contents, and do not return an empty
  plan.
- Each writer invocation owns one page. Do not draft sibling pages.

## Repository context (important)

- Your callable tools are exactly those listed in your task's toolset list.
  **Never call a tool that is not registered** — the call fails and wastes
  the budget needed to finish the page.
- For a pull request, read only the assigned repository at its stated PR head
  SHA. Use the supplied evidence before requesting another repository read.
- Do not assume a filesystem. A pull-request run has no local checkout, while an
  evaluation run reads a checkout; both expose their repository reads through
  the read tools named in your toolset list. Decide what exists from that list,
  never from this skill, and read the current page content with it.
- Emit tool calls in **small batches (at most 6 per response)**. A very large
  batch of tool calls in one response gets cut off mid-JSON by the model's
  per-response output cap, and the interrupted calls are discarded.

## Output

Produce a `DocChangePlan` for one assigned page. Its `files` entry is metadata only:
`{path, action}`. Put the Markdown body only through `append_chunk` and call
`finalize_draft` before returning the plan. Never inline file content in the plan.

## References

Read on demand with your file tools — load only when needed:

- `references/change-management.md` — update workflow from detection to validation; follow when planning and executing the update
- `references/backward-compatibility.md` — versioning and deprecation guidance; load when the change affects users on older versions
