---
name: documentation-generation
description: Generates new documentation pages with proper structure, frontmatter, and internal links. Use when research confirms a topic is undocumented and the action is create.
allowed-tools: analyze_structure extract_frontmatter validate_links read_file
metadata:
  references: 4
  assets: 4
---

# Documentation Generation

## Purpose

Create new documentation pages that are complete, accurate, and follow the
project's conventions.

## Steps

1. Confirm the topic is genuinely undocumented (see documentation-research).
2. Plan the page structure with `analyze_structure`-friendly headings.
3. Write the page: title, frontmatter (`title`, `description`, `tags`), overview,
   body sections, and related links.
4. Validate frontmatter with `extract_frontmatter` and links with
   `validate_links`.
5. Produce a `DocChangePlan` with the file path, content, and commit message.

## Guidelines

- Match the existing docs directory layout and naming.
- Every claim must be grounded in evidence from research.
- Include a short "Prerequisites" or "Overview" before deep technical sections.
- If evidence is thin, stop and re-research instead of padding with generic
  content.
- When a template conflicts with the repo's existing conventions, the repo
  wins — note the deviation in the plan summary.

## Output

A `DocChangePlan` (`repository`, `branch`, `files[{path, content,
action: "create"}]`, `commit_message`, `summary`) — one plan per topic, even
if it creates multiple files.

## References

Read on demand with your file tools — load only when needed:

- `references/documentation-standards.md` — required page structure and elements; load when planning the structure (step 2)
- `references/technical-writing-rules.md` — evidence requirements per claim type; load while writing body content (step 3)
- `references/documentation-style-guide.md` — voice, tone, and formatting conventions; load before writing prose
- `references/code-example-guidelines.md` — standards for runnable, minimal examples; load when including code blocks

## Assets

Use as the skeleton matching the page type; copy at the start of step 3:

- `assets/conceptual-template.md` — for concept/explanation pages
- `assets/how-to-template.md` — for task-oriented how-to pages
- `assets/tutorial-template.md` — for beginner tutorials
- `assets/api-reference-template.md` — for API reference pages