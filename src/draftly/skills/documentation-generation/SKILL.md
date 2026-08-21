---
name: documentation-generation
description: Generates new documentation pages with proper structure, frontmatter, and internal links.
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