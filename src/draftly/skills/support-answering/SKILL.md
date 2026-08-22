---
name: support-answering
description: Answers support questions accurately and concisely, grounded in evidence. Use for triaged questions routed for a direct answer.
allowed-tools: semantic_search keyword_search hybrid_search read_file search_messages get_thread
metadata:
  references: 3
  assets: 0
---

# Support Answering

## Purpose

Write answers that actually solve the developer's problem.

## Steps

1. Understand the exact question (re-read it and the thread context).
2. Research the answer using search tools and documentation.
3. Verify the answer against code/evidence — do not guess.
4. Write a concise answer with concrete steps, config snippets, or doc links.
5. Cite sources so the developer can verify.

## Guidelines

- If the docs are wrong, say so and flag the gap — do not work around it.
- Answers should be skimmable: short paragraphs, steps, one code block.
- If no citable source supports the answer, route to uncertainty handling —
  do not answer from general knowledge.
- Match the question's depth: a quick config question gets a short answer,
  not a tutorial.

## Output

A `SupportAnswer` (`answer_id`, `question_id`, `content`, `confidence`,
`citations[]`, `grounded`) ready for support-evaluation.

## References

Read on demand with your file tools — load only when needed:

- `references/answer-policy.md` — core answering principles; load before writing the answer (steps 3–5)
- `references/citation-policy.md` — citation formats; apply in step 5
- `references/uncertainty-policy.md` — uncertainty triggers and review routing; load when confidence is low