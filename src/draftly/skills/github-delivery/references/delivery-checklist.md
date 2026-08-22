# Delivery Checklist

Verification checklist for documentation changes before and after GitHub delivery. Used by the `github-delivery` skill to ensure quality and completeness.

## Pre-Delivery Validation

### Content Quality
- [ ] All Markdown renders without errors (headings, tables, code blocks, links)
- [ ] No broken internal links (run link checker against doc site)
- [ ] No broken external links (HTTP 200 for all external refs)
- [ ] Code examples are syntactically correct (language-appropriate lint)
- [ ] Code examples use current API versions (no deprecated patterns)
- [ ] All placeholder values replaced (no `TODO`, `FIXME`, `<example>`)

### Completeness
- [ ] Every affected area from `ImpactAnalysis` has corresponding doc change
- [ ] Breaking changes include migration guide with before/after examples
- [ ] Deprecations include timeline and alternative with code example
- [ ] New features include at least one working example
- [ ] API changes include updated request/response schemas
- [ ] CLI changes include updated command reference and examples

### Consistency
- [ ] Terminology matches project glossary
- [ ] Style matches documentation style guide (voice, tense, formatting)
- [ ] Cross-references use relative links (not absolute URLs)
- [ ] Version references consistent (no hardcoded versions where dynamic)
- [ ] Frontmatter/metadata present where required (title, description, tags)

### Metadata
- [ ] Commit message follows `commit-policy.md` format
- [ ] PR title follows `pull-request-policy.md` format
- [ ] PR body includes all required sections
- [ ] Labels applied per `pull-request-policy.md`
- [ ] `Refs` and `Source` trailers in commit message

## Post-Delivery Verification

### PR Health
- [ ] PR created successfully (201 response)
- [ ] Branch exists and matches committed files
- [ ] PR is not draft (unless source was draft/prerelease)
- [ ] Required reviewers assigned per `pull-request-policy.md`
- [ ] Labels present: `documentation`, `automated`, + conditional

### Traceability
- [ ] PR description links to source event (issue/PR/release URL)
- [ ] Commit `Refs` trailer matches webhook `event_id`
- [ ] Commit `Source` trailer matches source type + ID
- [ ] Delivery reference (PR number) recorded in workflow state

### Automated Checks
- [ ] CI passes (markdown lint, link check, spell check)
- [ ] Preview deployment succeeds (if configured)
- [ ] No merge conflicts with base branch
- [ ] Branch protection satisfied (if any)

## Rollback Criteria

If ANY of these fail, do not merge — fix and re-deliver:
- Broken links in changed files
- Markdown render errors
- Missing migration guide for breaking change
- Incorrect code examples
- PR fails CI checks
- Reviewer requests changes

## Re-Delivery

To re-deliver after fixes:
1. Update files in working branch
2. Amend commit (preserve `Refs`/`Source` trailers)
3. Force-push branch (`--force-with-lease`)
4. Update PR body if scope changed
5. Re-run verification checklist

## Success Metrics

Recorded per delivery:
- `delivery_time_ms`: End-to-end delivery duration
- `files_changed`: Count of files in commit
- `lines_added` / `lines_removed`: Diff stats
- `pr_number`: For tracking
- `review_time_hours`: Time to first review (post-merge)
- `merge_time_hours`: Time to merge (post-approval)