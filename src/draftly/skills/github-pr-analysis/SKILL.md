---
name: github-pr-analysis
description: Analyzes GitHub pull requests to determine documentation impact, change type, and affected areas. Use when a pull_request webhook arrives.
allowed-tools: get_pull_request get_diff get_files semantic_search keyword_search hybrid_search
metadata:
  references: 3
  assets: 0
---

# GitHub PR Analysis

## Purpose

Analyze a GitHub pull request (title, body, diff, changed files) to determine
whether documentation must be updated, created, or answered.

## Steps

1. Fetch the pull request with `get_pull_request`, then `get_diff` and
   `get_files` to inspect the actual changes.
2. Classify the change type: `documentation_only`, `bug_fix`, `new_feature`,
   `api_change`, `breaking_change`, or `deprecation`.
3. Identify affected product areas and map each to the documentation that
   covers it (via `semantic_search` / `keyword_search` / `hybrid_search`).
4. Produce an `ImpactAnalysis`:
   - `update`: affected docs exist and must change.
   - `create`: docs are missing entirely.
   - `answer`: the PR is a question, not a docs change.
   - `none`: no documentation impact.

## Guidelines

- API changes and breaking changes are HIGH urgency — recommend review.
- Never guess the impact of files you cannot inspect; fetch the diff.
- Cite concrete file paths and doc ids in `evidence`.
- For very large diffs, prioritize files under public API/config surfaces and
  say which files you sampled.
- Documentation-only PRs get action `none` — do not loop docs work back onto
  itself.

## Output

An `EventClassification` (`surface: "pull_request"`, `change_type`, `urgency`,
`reason`) plus an `ImpactAnalysis` (`action`, `affected_documents[]`,
`rationale`, `evidence[]`).

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
- `references/change-impact-rules.md` — changed-file-to-doc mapping heuristics; apply in step 3
- `references/documentation-impact.md` — action decision rules (`update`/`create`/`answer`/`none`); apply in step 4
