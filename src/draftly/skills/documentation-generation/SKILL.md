---
name: documentation-generation
description: Generates new documentation pages with proper structure, frontmatter, and internal links. Use when research confirms a topic is undocumented and the action is create.
allowed-tools: analyze_structure extract_frontmatter validate_links
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

## Repository context (important)

- You only have the tools listed under `allowed-tools` above. **Never call
  tools that are not in this list** — they are not registered in this run and
  the call fails.
- In pull-request (GitHub) runs there is **no local checkout**: `read_file`
  and `list_directory` do not exist for you. Repository context arrives via
  the PR surface tools the run registers (`get_diff`, `get_files`,
  `affected_docs`); use them instead of expecting a filesystem.
- Emit tool calls in **small batches (at most 6 per response)**. A very large
  batch of tool calls in one response gets cut off mid-JSON by the model's
  per-response output cap, and the interrupted calls are discarded.

## Drafting

- Write incrementally with the draft tools provided for this run
  (`start_draft`, `append_chunk`, `finalize_draft`): `append_chunk` one
  logical section per call, keeping each chunk well under the 24_000-char cap
  (~6_000 chars is a good target).
- Only `finalize_draft` after real content has been appended — a zero-byte
  sealed draft is a failure. If you have nothing grounded to write, stop and
  re-research instead of sealing an empty file.

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