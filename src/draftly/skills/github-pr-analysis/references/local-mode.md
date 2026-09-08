# Local Mode (offline/harness runs)

This skill normally drives evidence collection from the GitHub API. In LOCAL
mode that API is absent — do NOT call `get_pull_request`, `get_diff`,
`get_files`, or any GitHub web tool: the network endpoint is unavailable and
calling it wastes turns on 401s.

WORKING MODE IS DETECTED BY YOUR TOOLSET: if the GitHub web tools are NOT in
your available tool list, you are in local mode.

## What to do instead

1. The PR title, body, diff hunks, and changed-file list are ALREADY in the
   task context. Start from those — never claim you lack the diff.
2. Confirm the changes against the local checkout with the repository tools
   you DO have (`code_search`, `read_file`, `list_directory`, `git_*`), always
   passing `repo_dir=<local checkout path>`.
3. Collect concrete evidence: file paths + line ranges under the checkout, and
   doc ids found via `semantic_search` / `keyword_search` / `hybrid_search`.

## Classification in local mode

- Skip nothing: classify with full effort exactly as in API mode, using the
  task-context diff instead of `get_diff`.
- `documentation_only` still means `none` — do not loop docs work onto itself.
- Never inspect source you cannot reach from the repo root the checkout
  exposes; if a path in the diff is unresolved, mark it in `rationale` rather
  than guessing.