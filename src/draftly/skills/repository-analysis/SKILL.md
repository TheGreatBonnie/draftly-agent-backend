---
name: repository-analysis
description: Explores a repository's structure, code, and git history to ground documentation work in reality. Use before writing or updating docs for a repo area.
allowed-tools: list_directory read_file file_exists code_search git_log git_diff
metadata:
  references: 3
  assets: 0
---

# Repository Analysis

## Purpose

Understand the repository before writing or updating its documentation.

## Steps

1. List the repository root and `docs/` directory to find existing docs.
2. Use `code_search` to locate implementation of the affected features.
3. Inspect git history with `git_log` / `git_diff` to understand recent changes.
4. Read key files with `read_file` and record their structure.

## Guidelines

- Ground every claim in code you actually inspected.
- Never reference files you have not read.
- Record exact paths so writers can produce correct doc links.
- In large repositories, scope exploration to the affected paths — do not
  enumerate the whole tree.
- Skip generated and vendored code; it is not a documentation source.

## Output

An `EvidenceBundle` (`items[]` of file paths, symbols, snippets, and history
notes; `summary`) that downstream writers can cite verbatim.

## References

Read on demand with your file tools — load only when needed:

- `references/repository-conventions.md` — expected directory layout, naming, and organization; load during step 1 exploration
- `references/code-to-doc-mapping.md` — rules for mapping code elements to doc artifacts; load in steps 2 and 4
- `references/source-of-truth-policy.md` — authority hierarchy across sources; load when sources disagree
