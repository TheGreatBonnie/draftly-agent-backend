---
name: documentation-update
description: Updates existing documentation pages to reflect code changes, keeping diffs minimal and accurate.
---

# Documentation Update

## Purpose

Modify existing documentation to stay accurate with the codebase.

## Steps

1. Identify the exact sections that changed using `find_section`.
2. Read the current content and the relevant diff/code.
3. Apply minimal, surgical edits — do not rewrite untouched sections.
4. Re-validate frontmatter and links afterward.
5. Produce a `DocChangePlan` with the modified files.

## Guidelines

- Small focused diffs are easier to review — prefer them.
- Preserve the original author's voice and structure.
- Never remove working examples without replacing them.