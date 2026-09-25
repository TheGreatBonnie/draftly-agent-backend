---
name: github-pr-analysis
description: Analyzes GitHub pull requests to determine documentation impact, change type, and affected areas. Use when a pull_request webhook arrives.
allowed-tools: get_pull_request get_diff get_files semantic_search keyword_search hybrid_search
metadata:
  references: 4
  assets: 0
---

# GitHub PR Analysis

## Purpose

Analyze a GitHub pull request (title, body, diff, changed files) to determine
whether documentation must be updated, created, or answered.

## Mode

Working mode is detected by your toolset. If `get_pull_request` / `get_diff` /
`get_files` are in your available tools, you are in GITHUB (API) mode — fetch
from the API. If they are NOT, you are in LOCAL mode: the PR/diff/changed files
are already in the task context; confirm them against the checkout with repo
tools (`repo_dir=<local checkout path>`) and never call the GitHub API. Read
`references/local-mode.md` before researching in local mode.

## Steps

1. In GITHUB mode: fetch the pull request with `get_pull_request`, then
   `get_diff` and `get_files` to inspect the actual changes. In LOCAL mode:
   start from the diff hunks and changed-file list provided in the task
   context (see `references/local-mode.md`).
2. Classify the change type: `documentation_only`, `bug_fix`, `new_feature`,
   `api_change`, `breaking_change`, or `deprecation`.
3. Identify affected product areas and map each to the documentation that
   covers it (via `semantic_search` / `keyword_search` / `hybrid_search`).
   Call the search tools with SCALAR arguments only —
   `{"query": "<plain string>", "namespace": "<plain string>", "limit": <int>}`.
   A JSON array for `query`/`namespace` is rejected at tool binding. If
   searches return no coverage, enumerate the repository docs tree with your
   repo tooling — in GITHUB mode `github_get_tree` / `github_read_file`; in
   LOCAL mode your checkout repo tooling (see `references/local-mode.md`) —
   and map each changed symbol/area onto the ACTUAL pages found.
   Never name a documentation path you did not observe in the tree.
4. Produce an `ImpactAnalysis`:
   - `update`: affected docs exist and must change.
   - `create`: docs are missing entirely.
   - `answer`: the PR is a question, not a docs change.
   - `none`: no documentation impact.

## Guidelines

- API changes and breaking changes are HIGH urgency — recommend review.
- Never guess the impact of files you cannot inspect; fetch the diff.
- A search-tool binding error (e.g. a list passed where a string is required)
  means the call was malformed — re-issue it with scalar args; it is not a
  "no results" signal. Empty/absent results mean the topic is unindexed —
  fall back to repo-tree enumeration, not to guessing doc paths.
- In LOCAL mode, never call GitHub web tools — the API is unavailable; use the
  task-context diff and repo tooling (see `references/local-mode.md`).
- Cite concrete file paths and doc ids in `evidence`.
- For very large diffs, prioritize files under public API/config surfaces and
  say which files you sampled.
- Documentation-only PRs get action `none` — do not loop docs work back onto
  itself.

## Output

An `EventClassification` (`surface: "pull_request"`, `change_type`, `urgency`,
`reason`) plus an `ImpactAnalysis` (`action`, `affected_documents[]`,
`rationale`, `evidence[]`). Each `evidence[]` entry carries
`{id, url, topic, excerpt}` — give every entry a `topic` so the coverage gate
can match it against the authored prose.

## Example

Input: PR #412 renames `client.connect()` options (`retries` →
`max_retries`) in the SDK's connection module.

Output: classification `{change_type: "api_change", urgency: "high"}`;
analysis `{action: "update", affected_documents:
["docs/api/client.md", "docs/getting-started.md"], rationale: "renamed option
referenced in both pages", evidence: ["src/client.py:L120",
"docs/api/client.md#connection-options"]}`.

## References

Read on demand with your file tools — load only when needed:

- `references/pr-analysis-rules.md` — change type classification table; apply in step 2
- `references/local-mode.md` — offline/local grounding rules; read before researching when your toolset has no GitHub web tools
- `references/change-impact-rules.md` — changed-file-to-doc mapping heuristics; apply in step 3
- `references/documentation-impact.md` — action decision rules (`update`/`create`/`answer`/`none`); apply in step 4
