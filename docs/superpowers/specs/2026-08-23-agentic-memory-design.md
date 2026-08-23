# Agentic Memory Design — Spec

Date: 2026-08-23
Status: Approved (design review in chat, 2026-08-23)
Scope: `draftly-agent-backend`
Reference: `reference/agentic-memory-design.md` (repo root)

## Problem

Draftly agents must remember what they learn about a software project across
runs, and must stop trusting old knowledge when the software changes. The
backend already implements roughly 60% of the agentic memory reference design:

**Existing (keep as-is):**

- NeonDB schema: `memory_items` (status/importance/confidence/version/
  access tracking) plus supporting tables `memory_embeddings`,
  `memory_sources`, `memory_links`, `memory_feedback`,
  `memory_access_log`, `memory_consolidations` (migrations 002–009).
- `MemoryService` facade with remember / forget / recall /
  recall_knowledge / consolidate / curate_namespace
  (`src/draftly/memory/service.py`).
- Semantic retrieval + composite re-ranking
  (`src/draftly/memory/retrieval.py`, `ranking.py`) per the curation policy.
- `MemoryGroundedNode` prepending recalled knowledge to context nodes of the
  documentation, support, and issue graphs
  (`src/draftly/orchestration/graphs/*.py`).

**Gaps vs the reference design:**

1. `build_memory_curator()` is a stub — a prompt with **no tools**, not wired
   into any workflow (`src/draftly/agents/shared/memory_curator.py`; empty
   tool scope at `src/draftly/app/composition/tools.py`).
2. No contradiction resolution or SUPERSEDE flow: `consolidate()` only bumps
   importance; superseded facts are never marked stale.
3. Retrieval does not filter by `status` — superseded/archived knowledge can
   resurface in grounding.
4. No episodic memory (trigger → actions → outcome episodes), so future runs
   cannot learn "last time this file changed, these docs were updated".
5. No procedural memory (learned investigation patterns).
6. No documentation knowledge graph: `memory_links` exists but code ↔ concept
   ↔ doc relationships are never built.
7. Workflows emit no structured memory candidates after completion.

## Goals

1. **Persistence** — episodic, procedural, and doc-graph knowledge survive
   across agent runs and feed future work.
2. **Adaptation** — new evidence supersedes stale facts before they resurface;
   retrieval never returns non-active semantic memory.
3. **Reliability** — every curated record traces back to evidence (provenance
   via candidates + sources).
4. An autonomous **Memory Curator** that receives candidates asynchronously
   and decides CREATE / UPDATE / MERGE / SUPERSEDE / REJECT / ARCHIVE.
5. Zero added user-facing latency: curation runs post-run on existing worker
   infrastructure; extraction is deterministic packaging only.

## Non-goals

- Strands `SessionRepository` for conversation-history persistence (working
  memory stays ephemeral per-run invocation state). Revisit later if support
  threads need continuity.
- A dedicated graph database — relationships live in relational tables.
- Rewriting the existing semantic memory path beyond status filtering and the
  supersede operation.
- Real-time (inline) curation during workflows.

## Approach (approved)

Dedicated subsystems: each long-term memory kind gets its own tables and
service, alongside the untouched semantic path. Workflows write structured
candidates to an outbox table; an async curation workflow consumes them with a
tooled curator agent. Chosen over namespace-driven extension of
`memory_items` (polymorphic overloading of one table) and over Strands-session
centric designs (session managers persist conversation history, not curated
knowledge).

Central principle (from the reference):

> Draftly should not remember events. Draftly should remember knowledge
> extracted from events, preserve the evidence behind that knowledge, and
> continuously update that knowledge as the software evolves.

## Components

### 1. Migrations (028–031)

**028_episodes.sql** — what happened:

```sql
CREATE TABLE IF NOT EXISTS episodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id TEXT REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,
    agent_run_id UUID REFERENCES agent_runs(id),
    trigger_type TEXT NOT NULL,          -- github_pr | push | release | slack | discord | manual
    trigger_id TEXT,
    trigger_summary TEXT NOT NULL,
    actions_taken JSONB NOT NULL DEFAULT '[]'::JSONB,
    tools_used TEXT[] NOT NULL DEFAULT '{}',
    outcome TEXT NOT NULL CHECK (outcome IN ('success','failure','partial')),
    evaluation_results JSONB,
    artifacts_created JSONB NOT NULL DEFAULT '[]'::JSONB,
    summary TEXT,
    embedding VECTOR(1536),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_episodes_org_trigger ON episodes (org_id, trigger_type, created_at DESC);
CREATE INDEX idx_episodes_embedding ON episodes USING hnsw (embedding vector_cosine_ops);
```

**029_procedures.sql** — learned how-to knowledge:

```sql
CREATE TABLE IF NOT EXISTS procedures (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id TEXT REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    pattern_description TEXT NOT NULL,
    trigger_conditions JSONB NOT NULL DEFAULT '{}'::JSONB,
    steps JSONB NOT NULL DEFAULT '[]'::JSONB,
    applicability_context TEXT,
    success_count INT8 NOT NULL DEFAULT 0,
    failure_count INT8 NOT NULL DEFAULT 0,
    confidence FLOAT8 NOT NULL DEFAULT 0.5
        CONSTRAINT procedures_confidence_check CHECK (confidence >= 0 AND confidence <= 1),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
    embedding VECTOR(1536),
    last_applied_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_procedures_org_status ON procedures (org_id, status);
CREATE INDEX idx_procedures_embedding ON procedures USING hnsw (embedding vector_cosine_ops);
```

**030_doc_relations.sql** — software ↔ docs knowledge graph:

```sql
CREATE TABLE IF NOT EXISTS knowledge_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id TEXT REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,
    node_type TEXT NOT NULL CHECK (node_type IN ('code','concept','doc','eval')),
    key TEXT NOT NULL,                   -- e.g. 'auth/token_service.py', 'docs/auth/tokens.md'
    title TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, node_type, key)
);

CREATE TABLE IF NOT EXISTS doc_edges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id TEXT REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,
    source_node_id UUID NOT NULL REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
    target_node_id UUID NOT NULL REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL CHECK (relation_type IN
        ('IMPLEMENTS','DOCUMENTED_BY','AFFECTS','DERIVED_FROM')),
    evidence JSONB NOT NULL DEFAULT '[]'::JSONB,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_node_id, target_node_id, relation_type)
);
```

**031_memory_candidates.sql** — async curation outbox:

```sql
CREATE TABLE IF NOT EXISTS memory_candidates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id TEXT REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,
    candidate_type TEXT NOT NULL CHECK (candidate_type IN
        ('fact','decision','procedure_pattern','doc_relation','episode_summary')),
    payload JSONB NOT NULL,
    source_type TEXT,
    source_id TEXT,
    evidence JSONB NOT NULL DEFAULT '[]'::JSONB,
    confidence FLOAT8 NOT NULL DEFAULT 0.5,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','processing','applied','rejected')),
    decision_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ
);
CREATE INDEX idx_memory_candidates_pending ON memory_candidates (status, created_at)
    WHERE status = 'pending';
```

### 2. Services layer

New modules mirroring the existing `memory/service.py` pattern (thin facade
over a store, structlog logging, fail-open consumers):

```
src/draftly/memory/episodic/store.py       EpisodicStore
src/draftly/memory/episodic/service.py     EpisodicService
src/draftly/memory/procedural/store.py     ProceduralStore
src/draftly/memory/procedural/service.py   ProceduralService
src/draftly/memory/docgraph/store.py       DocGraphStore
src/draftly/memory/docgraph/service.py     DocGraphService
src/draftly/memory/candidates/store.py     CandidateStore
src/draftly/memory/candidates/models.py    MemoryCandidate (pydantic)
src/draftly/memory/candidates/service.py   CandidateService
```

Public surfaces:

- `EpisodicService.record_episode(run_result) -> dict` — called by
  `WorkflowRunner` after every job; embeds summary, writes row.
  `find_similar(query, *, org_id, limit=5) -> list[dict]` — cosine search
  over episode embeddings.
- `ProceduralService.match(context_query, *, org_id, limit=3)` /
  `.reinforce(procedure_id)` / `.invalidate(procedure_id)` — success/failure
  counters drive confidence; a procedure auto-archives when confidence falls
  below 0.3 after at least 3 recorded applications.
- `DocGraphService.upsert_node(...)`, `.link(source_key, target_key,
  relation_type, evidence)` (idempotent upsert bumping `last_confirmed_at`),
  `.affected_docs(code_paths, *, org_id)` — traversal returning documented-by
  docs for changed code paths.
- `CandidateService.enqueue(candidate)` / `.claim_batch(limit)` (atomic
  `pending → processing` transition) / `.mark_applied(id, decision_reason)` /
  `.mark_rejected(id, reason)`.

### 3. Candidate extraction

New module `src/draftly/workflows/post_run/candidate_extractor.py`, invoked by
`WorkflowRunner` on completion. Deterministic packaging only — no LLM call:

- Research/impact findings → `fact` / `decision` candidates.
- Successful runs touching repeated file↔doc pairs → `doc_relation`
  candidates.
- Successful runs matching known patterns → `procedure_pattern` candidates.
- Run outcome summaries → stored directly as episodes (not candidates).

Extraction and grounding fail-open: any exception logs and continues — memory
must never break a documentation run.

### 4. Memory Curator workflow (async)

`src/draftly/workflows/memory/curation_workflow.py`, scheduled on the
existing worker/scheduler infrastructure:

1. `CandidateService.claim_batch()` — claim pending candidates.
2. Invoke the curator agent built by `build_memory_curator` — now given real
   tools registered under the existing `memory_curator` scope in
   `composition/tools.py`:
   - `memory_search(namespace, query)` — find related/similar records
   - `get_memory(memory_id)` — inspect record + provenance
     (`memory_sources`)
   - `supersede_memory(old_id, new_content, evidence)` — set old record
     `status='superseded'`, create replacement, record provenance
   - `reinforce_memory(memory_id)` — confidence bump on corroboration
   - `merge_memories(ids)` — reuse consolidation path +
     `memory_consolidations`
   - `archive_memory(memory_id)` — soft eviction
   - `record_doc_relation(...)` / `record_procedure(...)` — write to the new
     subsystems
3. Curator returns a structured decision per candidate
   (CREATE / UPDATE / MERGE / SUPERSEDE / REJECT / ARCHIVE); the workflow
   applies it transactionally and marks the candidate applied/rejected with a
   reason.

Contradiction handling follows the reference flow: investigate staleness vs
environment-specific values vs authority of evidence, then UPDATE or KEEP BOTH
with conditions (e.g. production vs free-tier values as separate scoped
records).

Curator crashes leave candidates `pending`/`processing` for retry — no data
loss.

### 5. Supersede semantics for the semantic layer

- `VectorSearch.search()` gains a required `status='active'` filter on
  `memory_items`.
- New `MemoryService.supersede(old_id, new_item, evidence)` implementing the
  lifecycle transition; superseded records remain explicitly queryable but
  never surface in grounding.
- This closes the stale-fact risk that motivated the design.

### 6. Consumption upgrades

- `MemoryGroundedNode._ground()` merges three ranked sources into one compact
  block, total budget stays `MAX_GROUNDING_ITEMS = 5`:
  1. semantic facts via existing `recall_knowledge`
  2. `EpisodicService.find_similar(task)` — episodic transfer ("last time
     this file changed…")
  3. `ProceduralService.match(task)` — applicable playbooks
- Impact-analysis agent gains `affected_docs(code_paths)` as a tool so PR
  events resolve through the doc graph deterministically.
- Working memory unchanged: Strands per-run invocation state. (Matches Strands
  guidance: session managers handle conversation persistence; curated
  long-term context arrives via runtime retrieval.)

### 7. Forgetting & maintenance jobs

On the existing scheduler:

- **Soft eviction**: status transitions only
  (`active → superseded → archived`); archived excluded from retrieval,
  queryable explicitly.
- **Archival compression** (monthly): episodes older than 180 days collapse
  into single summary memories; originals archived.
- **Hard delete**: only via curator REJECT/dedup paths — raw workflows never
  delete.
- **Weekly hygiene**: extends `curate_namespace()` to demote stale
  low-importance items per the curation policy skill.

## Error handling

- Grounding, extraction, and maintenance jobs fail-open (log + continue).
- Curator failures leave candidates unapplied for retry.
- All writes carry org scoping; no cross-org leakage possible through any new
  service.

## Testing

TDD per repo conventions; unit tests against fake stores, integration tests
against migrations:

1. Services: each new service's public surface; supersede state machine;
   confidence math for procedures; idempotent doc-edge upserts.
2. Candidate extractor: packaging correctness from representative run results;
   no LLM calls.
3. Curation workflow end-to-end with a stubbed model: CREATE / MERGE /
   SUPERSEDE / REJECT decisions land in the right tables; candidate status
   transitions correct; crash mid-batch leaves retryable state.
4. Regression: `MemoryGroundedNode` output identical when new services are
   disabled/unavailable.
5. Retrieval: superseded/archived records excluded from `VectorSearch`.

Verification: `ruff check`, `mypy`, full pytest suite; `graphify update .`
after code changes.

## Risks / notes

- Four new tables + services is more plumbing than the namespace-driven
  alternative; accepted deliberately for cleaner typed shapes per memory kind
  (user-approved trade-off).
- Embedding dimension fixed at 1536 to match existing embeddings usage;
  confirm against `draftly.memory.embeddings` at implementation time and keep
  a single constant shared by both new tables.
- HNSW index choice follows pgvector best practice for approximate nearest
  neighbor at low-to-moderate scale; swap to IVFFlat if insert throughput
  becomes an issue.
- Curator tool-calling quality depends on the model tier routed for
  `memory_curator` (currently `fast` in `models/factory.py`); may need
  promotion to `standard` if decisions are poor in practice.

## References

- `reference/agentic-memory-design.md` — layered architecture, curator
  contract, memory operations, forgetting strategies.
- Strands Agents SDK docs: Session management
  (`https://strandsagents.com/docs/user-guide/concepts/agents/session-management/`)
  and Lesson 9, Persistent Memory With Session Managers — session managers
  persist conversation history; curated long-term context is a separate
  runtime-retrieved source. Custom storage backends remain available via the
  `SessionRepository` extension point (deferred).
