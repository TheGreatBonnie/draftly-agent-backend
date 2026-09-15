# README evidence audit

Audit date: 2026-09-15. Scope: the backend README, inspected against the current backend checkout and its sibling frontend. This is a source and documentation audit, not a live demonstration or benchmark report.

## Claim and artifact inventory

| Claim or artifact | Source | Evidence class | README treatment |
| --- | --- | --- | --- |
| Strands research agents use source-specific tools | [Research agents](../src/draftly/agents/shared/research.py) | implementation | Link concrete agent factories |
| Documentation graph coordinates research, impact, writing, revision, changelog, delivery | [Graph](../src/draftly/orchestration/graphs/documentation_graph.py) | implementation | Explain GraphBuilder and actual branches |
| Writers cannot directly deliver a documentation PR | `_WRITER_EXCLUDED_TOOLS` in the graph | implementation | Describe tool restrictions, not a universal security guarantee |
| Merged PRs trigger documentation processing | [GitHub route](../src/draftly/app/api/routes/github.py) | implementation | State non-merged PR events are skipped |
| Review can pause delivery or cancel it | [ReviewGate](../src/draftly/orchestration/hooks/review_gate.py), [policies](../src/draftly/orchestration/routing/policies.py) | implementation | Explain always/risky/never; do not promise automatic rework after rejection |
| Graph-level review owns documentation delivery approval | Graph constructs delivery with `hitl=False`; [delivery agent](../src/draftly/agents/shared/delivery.py) receives mutation tools | implementation | Show policy bypass and rejection in the diagram |
| Memory can be curated into persistent knowledge | [Curation workflow](../src/draftly/workflows/memory/curation_workflow.py), [candidate extraction](../src/draftly/workflows/post_run/candidate_extractor.py) | implementation | Say what happens when workflows run; do not claim observed improvement |
| Routing can consider historical task performance | [Router](../src/draftly/models/router.py), [lifecycle warm start](../src/draftly/app/lifecycle.py) | implementation | Bound claim to stored statistics; omit performance benefits without measurements |
| Prompts and packaged skills automatically rewrite themselves | No evidence established by this audit | unverified | Exclude; describe edits as developer work |
| OAuth documentation scenario | [Dataset](../src/draftly/evaluation/datasets/documentation.json) | illustration | Explicit illustrative label and expected coverage table |
| Groundedness and other evaluation dimensions | [Evaluation implementation](../src/draftly/evaluation/), [runtime evaluator](../src/draftly/orchestration/nodes/evaluate.py) | implementation | Separate LLM judgments from deterministic/heuristic checks |
| Measured pass rates, latency, cost, or time saved | No qualifying run artifact found in searched README, docs, reports, or runtime filenames | unverified | Publish no numerical result or efficiency claim |
| Recorded demo and delivered PR | The README links a YouTube demo and includes a local thumbnail; no delivered PR artifact was verified | partially verified | Preserve the demo link without treating it as benchmark evidence |
| UI image assets | The local demo thumbnail exists; frontend public assets contain a logo | illustration | Do not present design assets as measured execution evidence |
| Complete frontend experience | [Frontend README](../../draftly-agent-ui/README.md), frontend routes, and typed API client | implementation | Link setup and describe the review workspace; do not claim live validation |
| Open-source license | [LICENSE](../LICENSE) contains the MIT License text | implementation | Keep the MIT badge linked to the repository license |
| Hackathon judging and submission expectations | [Official hackathon page](https://agentsforhumans.devpost.com/), opened during this audit | observed run | Cite event page; identify Professional Agents as intended track only |

Searches included tracked files and an additional filesystem walk excluding `.git`, `.venv`, `node_modules`, and Python caches. Internal `.superpowers/sdd/` implementation reports and graph reports were found; they were not treated as live agent benchmark results. The audit does not establish that no evidence exists outside the searched workspace.

## Setup checklist and findings

| Area | Inspected source | Finding and README decision |
| --- | --- | --- |
| Python environment | [Manifest](../pyproject.toml), [main](../main.py) | Python 3.11+, uv; use `uv run` for native processes |
| Database | [Bootstrap](../scripts/bootstrap.py), [migrations](../src/draftly/persistence/migrations/) | Apply migrations to development DB; vector extension and 1,536-dimensional embeddings; bootstrap skips some duplicate errors and has no version ledger |
| Redis | [Compose](../docker-compose.redis.yml), [Settings](../src/draftly/app/config.py) | Plain Redis 7 lacks RediSearch; choose pgvector and disable semantic cache |
| Model access | [Model factory](../src/draftly/models/factory.py) | Bedrock requires AWS configuration; alternative providers are supported |
| Embedding access | `build_embedding_router` in model factory, [Bedrock provider](../src/draftly/models/providers/bedrock.py) | Separate registered OpenRouter, Requesty, or Orcarouter embedding provider required; no native Bedrock embedding implementation |
| Worker topology | [RQ worker](../workers/rq_worker.py), [lifecycle](../src/draftly/app/lifecycle.py), [Settings](../src/draftly/app/config.py) | `rq_enabled` defaults true; document separate native worker rather than implying API consumes queues |
| Authentication | [API auth](../src/draftly/app/api/auth.py), [Settings](../src/draftly/app/config.py) | Clerk configuration additional to API liveness; same application as frontend |
| GitHub connection | [GitHub route](../src/draftly/app/api/routes/github.py), Settings | App configuration, installed repository, webhook reachability, and private-key path required |
| Frontend | [Frontend setup](../../draftly-agent-ui/README.md) | Sibling app, Clerk keys, backend URL, onboarding |
| API liveness | [Health route](../src/draftly/app/api/routes/health.py), [API composition](../src/draftly/app/api/app.py) | `/api/health` returns status ok and service draftly; does not test models or delivery |
| Integrated outcome | Dataset and route implementation | Merged PR → run → required review → approved delivery is expected, not demonstrated here |
| Evaluation CLI | [Script](../scripts/run_evaluation.py) | `--live` and `--datasets` supported; inspect result status/errors because main returns zero after printing batch output |
| Evaluation portability | [Dataset directory](../src/draftly/evaluation/datasets/) | Machine-specific repository paths and organization/project identifiers require adaptation |
| API container | [Dockerfile.api](../docker/Dockerfile.api) | Runtime omits main.py; build also omits explicit README copy; document intended commands with packaging limitation |
| Worker container | [Dockerfile.worker](../docker/Dockerfile.worker) | Copies secrets into image; preserve operational details and explicitly identify sensitive image handling |

## Editorial decisions

- Keep the backend README as an entry point and link the in-repository frontend.
- Use one product walkthrough and one architecture diagram, including no-docs release routing and review bypass.
- Describe runtime evaluators separately from dataset-based Strands Evals; do not equate scores with factual guarantees.
- Use existing source and scenario links in place of unavailable recorded demonstrations.
- Keep provider, deployment, and worker alternatives in focused engineering guides.
- Reflect the repository's existing MIT license without making additional licensing claims.
- Existing architecture/deployment guides contain broader historical assertions; links provide further context, not blanket verification of every statement in those guides.

## Verification record

For the 2026-09-15 refresh:

- `git diff --check` passed for the documentation changes.
- Every local Markdown link, HTML link, and image target in the changed documents resolved on disk.
- The README contains one quickstart, one walkthrough, one Mermaid diagram, and balanced code fences in the intended order.
- `pandoc -f gfm -t html5 --standalone` rendered each changed Markdown document successfully.
- Setup, health, AgentCore, and Compose commands were checked against the current source and manifests. No migrations, service launches, image builds, live model calls, publication, or application test suite were run.
- The README links a public demo video. This local source audit did not revalidate the external video's availability or contents.

## Plan completion and remaining evidence

The README refresh keeps setup and product orientation near the top, preserves the implementation-grounded architecture description, and moves detailed operations into deployment guides. The scope remains documentation only; application and dataset behavior were not changed.

A generated output PR, measured runs with provenance, and a successful fresh setup remain project evidence outside this refresh. API and worker image packaging limitations remain documented rather than silently changed.
