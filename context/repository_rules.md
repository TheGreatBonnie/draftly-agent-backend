# Repository Rules

Conventions for how Draftly works inside a repository.

## Structure

- Source lives under a conventional layout (e.g. `src/`, `lib/`, `app/`).
- Documentation lives in a `docs/` directory unless the repo says otherwise.
- Tests live alongside the code or in a `tests/` directory.

## Writing Rules

- Never commit directly to the default branch.
- Always open documentation changes as pull requests on a feature branch.
- Keep documentation diffs minimal and reviewable.
- Include a commit message that references the originating event.

## Search

- Use repository-aware search for code and docs; scope by repository.
- Respect `.gitignore`; never read vendored or generated artifacts as source.

## Safety

- Never expose secrets, tokens, or private data in generated docs.
- Follow the security rules in security_rules.md.