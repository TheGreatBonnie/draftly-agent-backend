# Remediation Rules

## Purpose

Maps each failure category to specific, actionable revision directives for the write-revise loop.

## Remediation Mapping

### Grounding Failures

| Subtype | Directive | Tools/Actions |
|---------|-----------|---------------|
| `missing_citation` | "Add citations for all factual claims. Use `recall_knowledge(query)` to find sources." | `MemoryService.recall_knowledge()`, `code_search()` |
| `weak_citation` | "Replace weak citations with sources that directly support each claim. Verify quote/paraphrase accuracy." | Re-read cited files, find better evidence |
| `hallucination` | "Remove fabricated content. Every API, function, config must exist in codebase. Verify via `code_search`." | `code_search()`, `read_file()`, AST inspection |
| `stale_grounding` | "Update citations to current code version. Check `git_log` for recent changes to cited files." | `git_log()`, `git_diff()`, re-verify against HEAD |

**Revision Prompt Template**:
```
The draft has grounding issues: {specific_failures}.
For each claim without valid evidence:
1. Search the codebase for the actual implementation
2. Cite the exact file, function, and line numbers
3. If the claim is wrong, correct it based on code
```

### Completeness Failures

| Subtype | Directive | Tools/Actions |
|---------|-----------|---------------|
| `missing_section` | "Add the missing section: {section_name}. Follow the {doc_type} template structure." | Template reference, `read_file(template)` |
| `incomplete_coverage` | "Expand {topic} with: purpose, parameters, return values, errors, examples. Minimum 3 paragraphs." | `recall_knowledge()`, `code_search()` |
| `missing_examples` | "Add runnable code example for {feature}. Show: import, setup, basic usage, common options." | Extract from tests, write minimal snippet |
| `missing_parameters` | "Document all parameters for {function/class}. Include: name, type, required, default, description." | Extract from signature + docstring |

**Revision Prompt Template**:
```
The draft is missing required content: {missing_items}.
For each missing item:
1. Research the implementation using code_search and recall_knowledge
2. Write the section following the template
3. Include at least one code example
```

### Correctness Failures

| Subtype | Directive | Tools/Actions |
|---------|-----------|---------------|
| `wrong_signature` | "Fix function signature to match implementation. Verify: name, params, types, defaults, return type." | `read_file()`, AST parse, `code_search()` |
| `wrong_return_type` | "Correct return type annotation and description. Check actual return statements." | `read_file()`, trace return paths |
| `wrong_behavior` | "Rewrite behavior description to match actual code logic. Trace execution path." | `read_file()`, `git_log()` for intent |
| `wrong_config` | "Fix configuration option name/value. Check config schema and validation code." | `code_search(config_key)`, schema files |
| `factual_error` | "Correct the factual error. Provide accurate information with code citation." | `code_search()`, `recall_knowledge()` |

**Revision Prompt Template**:
```
The draft contains factual errors: {errors}.
For each error:
1. Read the actual implementation file
2. Verify the correct behavior/signature/config
3. Rewrite the incorrect statement with accurate info
4. Cite the exact source location
```

### Relevance Failures

| Subtype | Directive | Tools/Actions |
|---------|-----------|---------------|
| `off_topic` | "Rewrite to address the actual task: {task_description}. Remove unrelated content." | Re-read task, focus scope |
| `scope_violation` | "Trim content to requested scope: {scope}. Move extras to separate doc or appendix." | Identify scope boundaries |
| `wrong_audience` | "Adjust technical level for {audience}. {Add basics/remove jargon/add advanced details}." | Audience profile reference |

**Revision Prompt Template**:
```
The draft is off-topic: {details}.
Rewrite to focus on: {actual_task}.
Remove: {irrelevant_sections}.
Target audience: {audience}.
```

### Quality Failures

| Subtype | Directive | Tools/Actions |
|---------|-----------|---------------|
| `poor_structure` | "Reorganize using standard structure: Overview → Prerequisites → Steps → Reference → Troubleshooting." | Apply template, restructure headings |
| `unclear_writing` | "Rewrite unclear passages. Use: active voice, specific nouns, short sentences, define terms on first use." | Style guide rules |
| `formatting_issues` | "Fix markdown: code fences with language, proper heading hierarchy, working links, valid tables." | Markdown linter, link check |
| `tone_violation` | "Adjust tone: professional, concise, instructional. Avoid: marketing fluff, apologies, hedging." | Tone guidelines |
| `redundancy` | "Remove duplicate content. Consolidate repeated concepts into single authoritative section." | Diff sections, merge |

**Revision Prompt Template**:
```
The draft has quality issues: {issues}.
Apply fixes:
- Structure: follow {template}
- Clarity: active voice, specific, concise
- Formatting: valid markdown, proper headings
- Tone: professional, direct
- Deduplicate: merge overlapping sections
```

### Security Failures

| Subtype | Directive | Tools/Actions |
|---------|-----------|---------------|
| `secret_exposure` | "REMOVE all secrets immediately. Replace with placeholders: `YOUR_API_KEY`, `${ENV_VAR}`." | Regex scan, manual review |
| `pii_exposure` | "REMOVE PII. Use generic examples: `user@example.com`, `user-123`." | PII scanner, replace |
| `internal_leak` | "REMOVE internal paths/URLs. Use public docs URLs or relative paths only." | Path sanitization |

**Revision Prompt Template**:
```
CRITICAL: Security violation detected: {details}.
Immediate actions required:
1. Remove all sensitive data from draft
2. Replace with safe placeholders
3. Re-run security scan before any further processing
```

## Iteration Control

### Max Iterations
- **Default**: 3 revision cycles per draft
- **Critical failures**: 1 extra cycle if progress made
- **After max**: Escalate to human review with failure summary

### Progress Detection
- Compare failure counts: `current.total_failures < previous.total_failures`
- Category shift: dominant category changes → progress
- Score improvement: aggregate severity score decreases

### Escalation Criteria
```
ESCALATE IF:
- 3 iterations completed without resolution
- Critical failure persists after 2 iterations
- New critical failures introduced in revision
- Dominant category unchanged for 2+ iterations
```

### Escalation Payload
```json
{
  "draft_id": "uuid",
  "iterations": 3,
  "failure_history": [
    {"iteration": 1, "categories": {...}, "dominant": "correctness"},
    {"iteration": 2, "categories": {...}, "dominant": "correctness"},
    {"iteration": 3, "categories": {...}, "dominant": "grounding"}
  ],
  "blocking_failures": [...],
  "recommended_action": "human_review|rewrite_from_scratch|abandon"
}
```

## Revision Prompt Construction

### Base Template
```
You are revising a documentation draft that failed evaluation.

FAILURE SUMMARY:
- Total failures: {count}
- Dominant category: {category}
- By category: {category_counts}

SPECIFIC FAILURES:
{failure_details}

ORIGINAL TASK: {task_description}

CURRENT DRAFT: {draft_content}

REVISION DIRECTIVES:
{directives_per_failure}

CONSTRAINTS:
- Ground every claim in code (cite file:line)
- Follow {doc_type} template
- Max {max_iterations} iterations
- No secrets, PII, or internal paths
```

### Directive Injection
For each failure in `failure_details`:
```
- [{category}.{subtype}] {directive_from_table}
  Context: {failure.reason}
  Evidence needed: {specific_evidence_hint}
```

## Success Criteria

### Per-Category Exit Conditions
| Category | Exit Condition |
|----------|----------------|
| Grounding | All claims cited; citations verified against current code |
| Completeness | All required sections present per template; examples included |
| Correctness | Zero factual errors vs. code; signatures/config match |
| Relevance | 100% on-topic; scope matches request; audience appropriate |
| Quality | Structure valid; clear prose; correct markdown; professional tone |
| Security | Zero findings from secret/PII/internal scanners |

### Overall Pass
- All category exit conditions met
- No critical/high severity failures
- Aggregate severity score < threshold (default: 5)