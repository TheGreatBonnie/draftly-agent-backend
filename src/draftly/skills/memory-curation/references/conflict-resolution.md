# Conflict Resolution

## Purpose

Rules for handling contradictory or conflicting memory items during curation and retrieval.

## Conflict Types

### Direct Contradiction
Two items in same namespace assert opposite facts about same entity.
- Example: "API v2 uses Bearer tokens" vs "API v2 uses API keys"

### Stale Override
Newer item implicitly supersedes older without explicit versioning.
- Example: Configuration default changed from `true` to `false`

### Source Conflict
Same fact from sources with different quality tiers.
- Example: Official docs (tier 1) vs forum post (tier 3)

## Resolution Strategies

### 1. Version-Aware Merge (Preferred)
```
IF new_item.version > existing.version:
    REPLACE existing with new_item
ELIF new_item.version == existing.version:
    MERGE: keep higher importance, union metadata
ELSE:
    KEEP existing; log conflict for review
```
- Uses `memory_items.version` (default 1, incremented on update)
- Requires explicit version bumps by writers

### 2. Source Quality Precedence
When versions unavailable or equal:
```
Higher source_quality WINS
TIE → Higher importance WINS
TIE → More recent (created_at) WINS
TIE → Flag for human review
```

### 3. Namespace-Specific Rules

| Namespace | Strategy |
|-----------|----------|
| `knowledge` | Source quality precedence + version |
| `solutions` | Keep all; rank by feedback score |
| `conversations` | No conflict; each thread independent |
| `issues` | Latest status WINS (open/closed) |

## Detection & Handling

### During Consolidation (`MemoryService.consolidate`)
- If similarity >= 0.95 but content contradicts → **flag conflict**, do not auto-merge
- Store conflict in `memory_consolidations` with `status = 'conflict'`
- Notify via evaluation loop for human resolution

### During Retrieval (`MemoryRetrieval.retrieve`)
- Return top-ranked items per `MemoryRanking` composite score
- If top-2 items contradict AND both score > 0.7 → return both with `conflict: true` flag
- Downstream consumers decide presentation

### During Evaluation
- `FailureAnalyzer` categorizes "incorrect" failures
- Contradictory grounding evidence → `correctness` category
- Triggers re-curation of involved items

## Audit Requirements

Every conflict resolution records:
- `conflict_id` (UUID)
- `item_a_id`, `item_b_id`
- `resolution_strategy` used
- `winner_id` (or `null` if escalated)
- `resolved_by` (system/human)
- `resolved_at` timestamp
- `reason` for decision

## Escalation Threshold

- Auto-resolve: source quality diff >= 0.3 OR version diff >= 2
- Human review: all other conflicts
- After 3 unresolved conflicts on same topic → create `documentation` gap task