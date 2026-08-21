---
name: repository-analysis
description: Explores a repository's structure, code, and git history to ground documentation work in reality.
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
