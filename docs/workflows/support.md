# Support Workflow

> **Status:** Implemented
> **Date:** 2026-08-25
> **Scope:** Processing inbound developer support questions from Slack and Discord — triage, answering, evaluation, delivery, and resolution.

## 1. Overview

The support workflow handles developer questions arriving in Slack and Discord channels. It triages each question, generates an evidence-grounded answer, evaluates the answer for quality, and delivers it back to the originating thread. Questions that signal documentation gaps are routed to the feedback loop for follow-up.

The workflow has three surface-specific entry points (Slack, Discord) that share the same `WorkflowRunner`-based graph, plus a post-delivery resolution step.

```mermaid
flowchart TD
    Message["Slack / Discord Message"] --> Runner["WorkflowRunner"]
    Runner --> Graph["Support Graph"]
    Graph --> Triage["support-triage"]
    Triage --> Route{"Category"}
    Route -->|bug/usage| Answer["support-answering"]
    Route -->|doc_gap| Gap["Flag for Feedback"]
    Route -->|noise| Ignore["Skip"]
    Answer --> Eval["support-evaluation"]
    Eval -->|passed| Review{"Review Policy"}
    Eval -->|failed| Revision["evaluation-failure-analysis"]
    Review -->|always| Human["Human Review Gate"]
    Review -->|never| Deliver["support-delivery"]
    Human -->|approved| Deliver
    Deliver --> Thread["Answer Posted"]
    Thread --> Resolve["resolve_support_thread"]
```

## 2. Trigger

- **Slack message event:** `app_mention`, `message` in channels where Draftly is present.
- **Discord message event:** Messages in configured guild channels.
- **Event routing:** The `EventDispatcher` routes to `run_slack_support` or `run_discord_support`.

## 3. Flow

### Step 1: Triage

The `support-triage` skill classifies the incoming question:

| Category | Action |
|----------|--------|
| `bug_report` | Route to answering |
| `usage_question` | Route to answering |
| `doc_gap_signal` | Flag for feedback loop + attempt answer |
| `noise` | Skip (bot messages, Draftly's own replies) |

The triage skill searches for prior similar questions before classifying, and flags non-English questions for language-matched responses.

### Step 2: Answering

The `support-answering` skill:

1. Re-reads the question and thread context.
2. Researches the answer using semantic/keyword search and documentation.
3. Verifies the answer against code and cited evidence.
4. Writes a concise answer with concrete steps, config snippets, or doc links.
5. Cites sources so the developer can verify.

**Guidelines:**
- If docs are wrong, say so and flag the gap.
- Answers should be skimmable: short paragraphs, steps, one code block.
- No citable source → route to uncertainty handling, not general knowledge.

### Step 3: Evaluation

The `support-evaluation` skill scores the draft answer:

| Metric | Criteria |
|--------|----------|
| Correctness | Technically accurate |
| Completeness | Actually answers the question asked |
| Groundedness | Claims backed by cited sources |
| Helpfulness | Steps are actionable and unambiguous |

Borderline scores route to human review rather than auto-failing.

### Step 4: Review Gate

Based on the configured `review_policy`:

- **`always`** — All answers require human approval before delivery.
- **`risky`** — Only high-risk payloads (uncertain confidence, breaking changes) require review.
- **`never`** — Approved answers are delivered automatically.

### Step 5: Delivery

The `support-delivery` skill posts the answer back to the originating thread:

- Replies in-thread (never creates new channels/DMs).
- Checks for existing delivery references to prevent double-posting.
- Records a `DeliveryReceipt` with the message reference.

**Platform-specific delivery:**

| Platform | Method |
|----------|--------|
| Slack | `post_message` with `thread_ts` |
| Discord | `post_message` with `thread_id` |
| GitHub | `create_comment` on the issue/PR |

### Step 6: Resolution

The `resolve_support_thread` function updates the thread status:

- **`DELIVERED`** → Thread marked resolved.
- **`PENDING_REVIEW`** / **`FAILED`** → Thread left open for the feedback loop.

## 4. File Reference

### Workflow Implementation

- `src/draftly/workflows/support/slack_support_workflow.py` — Slack entry point
- `src/draftly/workflows/support/discord_support_workflow.py` — Discord entry point
- `src/draftly/workflows/support/support_resolution.py` — Post-delivery resolution
- `src/draftly/workflows/support/__init__.py` — Package exports

### Skills

- `src/draftly/skills/support-triage/SKILL.md` — Question classification
- `src/draftly/skills/support-answering/SKILL.md` — Answer generation
- `src/draftly/skills/support-evaluation/SKILL.md` — Answer quality scoring
- `src/draftly/skills/support-delivery/SKILL.md` — Thread delivery
- `src/draftly/skills/support-feedback-analysis/SKILL.md` — Pattern analysis

### References

- `src/draftly/skills/support-triage/references/support-taxonomy.md`
- `src/draftly/skills/support-triage/references/severity-rules.md`
- `src/draftly/skills/support-triage/references/escalation-rules.md`
- `src/draftly/skills/support-triage/references/confidence-rules.md`
