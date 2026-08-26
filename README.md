# Draftly

**Autonomous documentation engineering platform.**

Draftly is an event-driven, multi-agent backend that keeps documentation in sync with your codebase. It ingests events from GitHub, Slack, and Discord, routes them through orchestration graphs, and coordinates teams of specialized agents to analyze changes, draft updates, answer support questions, and learn from feedback — with a human review gate before anything ships.

## Features

- **GitHub change analysis** — analyzes pull requests, issues, and releases to detect documentation impact
- **Documentation generation & sync** — researches repositories and drafts conceptual docs, how-tos, tutorials, and API references from templates
- **Support agent** — triages and answers user questions in Slack and Discord, grounded in your indexed documentation
- **Feedback loop** — classifies recurring questions and gaps, prioritizes signals, and turns them into documentation improvements
- **Human-in-the-loop review** — a review gate and approval queue hold agent output until a reviewer signs off
- **Persistent memory** — vector-searchable knowledge base with curation, deduplication, and grounding checks
- **Evaluation framework** — scores outputs on groundedness, correctness, completeness, and relevance, with failure analysis for continuous improvement
- **Multi-provider model routing** — pluggable registry across Amazon Bedrock, Bedrock Mantle, OpenRouter, NVIDIA, Requesty, and Orcarouter

## Architecture

```mermaid
flowchart TD
    subgraph sources["Event sources"]
        GH["GitHub<br/>PRs · issues · releases"]
        SL["Slack"]
        DC["Discord"]
    end

    subgraph api["FastAPI app"]
        WH["Webhook routes"] --> DISP["Event dispatcher"]
    end

    subgraph runtime["Workflow runtime"]
        RUN["Workflow runner"] --> GRAPH["Orchestration graphs<br/>documentation · support · issue · feedback · evaluation"]
        GRAPH --> AGENTS["Agent teams<br/>analyzer · researcher · writer · reviewer · auditor"]
        AGENTS --> GATE{"Human review gate"}
    end

    subgraph platform["Platform services"]
        MEM[("Memory store<br/>Postgres + vector search")]
        EVAL["Evaluation framework"]
        MODELS["Model router<br/>Bedrock · Mantle · OpenRouter · NVIDIA"]
        REDIS[("Redis<br/>cache · streams · rate limit · state")]
    end

    GH --> WH
    SL --> WH
    DC --> WH
    DISP --> RUN
    AGENTS <--> MEM
    AGENTS --> MODELS
    AGENTS <--> REDIS
    GATE -- "approved" --> DELIVER["Delivery service<br/>GitHub PRs & comments · Slack · Discord"]
    GATE -- "changes requested" --> FEEDBACK["Feedback loop"]
    EVAL --> FEEDBACK
    FEEDBACK --> GRAPH
```

Events arrive via webhook routes under `/api/*`, are normalized by the dispatcher, and run through workflow graphs composed of specialized agents. Agent capabilities are packaged as [skills](src/draftly/skills) — self-contained rule sets for triage, research, writing, review, delivery, and memory curation. Nothing reaches users until it passes the review gate; rejected output feeds back into the knowledge base.

## Tech stack

| Layer         | Technology                                                 |
| ------------- | ---------------------------------------------------------- |
| Language      | Python 3.11, managed with [uv](https://docs.astral.sh/uv/) |
| API           | FastAPI + Uvicorn                                          |
| Agents        | [Strands Agents](https://github.com/strands-agents) SDK    |
| Database      | PostgreSQL (asyncpg / psycopg), NeonDB-compatible          |
| Cache/Stream  | Redis (semantic cache, event streams, rate limiting, distributed state, API cache, dashboard push) |
| Auth          | Clerk (JWT), Slack/Discord/GitHub app auth                 |
| Observability | structlog, tracing, metrics, audit logging                 |

## Project structure

```
draftly-agent-backend/
├── main.py                           # API entrypoint (uvicorn)
├── pyproject.toml                    # Dependencies and tooling configuration
├── uv.lock
├── .env.example                      # Environment variable template
├── Makefile
├── config/                           # Per-environment settings
│   ├── development.yaml
│   ├── staging.yaml
│   └── production.yaml
├── context/                          # Platform-wide policies and rules
│   ├── project_context.md
│   ├── architecture.md
│   ├── documentation_policy.md
│   ├── writing_style.md
│   ├── evaluation_rules.md
│   ├── human_review_policy.md
│   ├── support_policy.md
│   ├── security_rules.md
│   ├── repository_rules.md
│   └── diagram_rules.md
├── docker/                           # Container definitions (API + workers)
│   ├── Dockerfile
│   ├── Dockerfile.api
│   └── Dockerfile.worker
├── docs/
│   ├── architecture/                 # overview, system-design, event-driven,
│   │                                 # multi-agent, memory, feedback-loop,
│   │                                 # orchestration, documentation-pipeline,
│   │                                 # persistence, integrations, security,
│   │                                 # delivery, evaluation, review,
│   │                                 # observability, support, tools,
│   │                                 # redis, models-router
│   ├── agents/                       # agent-overview, subagents, skills
│   ├── api/                          # routes, webhooks
│   ├── workflows/                    # overview, documentation-sync, github-pr,
│   │                                 # github-issue, support, feedback-loop
│   └── deployment/                   # aws, cockroachdb, production, redis
├── infra/
│   └── aws/diagrams/architecture.md
├── scripts/                          # bootstrap, seed_demo, run_workflow,
│                                     # reindex, run_evaluation
├── simulation/
│   ├── README.md
│   ├── roadmap.md
│   └── scenarios/                    # oauth, pkce, api-key deprecation, rbac,
│                                     # token rotation, sdk breaking change
├── src/draftly/
│   ├── agents/
│   │   ├── documentation/            # analyzer, researcher, writer, reviewer, auditor
│   │   ├── github/                   # issue_analyzer, issue_researcher, issue_responder
│   │   ├── support/                  # question_analyzer, solution_researcher,
│   │   │                             # answer_writer, support_reviewer
│   │   ├── shared/                   # classifier, context, research, delivery,
│   │   │                             # github_delivery, memory_curator, memory_grounding
│   │   ├── draftly_agent.py          # Base agent
│   │   ├── subagents.py
│   │   ├── prompts.py
│   │   └── schemas.py
│   ├── app/
│   │   ├── api/
│   │   │   ├── app.py                # FastAPI application factory
│   │   │   ├── auth.py
│   │   │   ├── middleware/           # auth, errors, logging
│   │   │   └── routes/               # github, slack, discord, clerk, documentation,
│   │   │                             # support, evaluations, jobs, reviewers, health
│   │   ├── composition/              # Dependency wiring: agents, events, tools,
│   │   │                             # workers, workflows
│   │   ├── workers/                  # scheduler, task_runner, worker runtime
│   │   ├── config.py
│   │   ├── dependencies.py
│   │   └── lifecycle.py
│   ├── delivery/                     # Outbound: github, slack, discord, documentation
│   ├── documentation/                # indexer, generator, updater, validator,
│   │                                 # analyzer, repositories
│   ├── evaluation/
│   │   ├── evaluators/               # groundedness, correctness, completeness,
│   │   │                             # relevance, documentation_quality, deterministic
│   │   ├── datasets/
│   │   ├── runner.py
│   │   ├── failure_analyzer.py
│   │   ├── service.py
│   │   └── store.py
│   ├── events/
│   │   ├── dispatcher.py             # Event routing
│   │   ├── envelope.py · base.py · types.py
│   │   ├── github/                   # pull_request, issue, release, push
│   │   ├── support/                  # slack, discord
│   │   └── documentation/            # document_changed, review_completed,
│   │                                 # publish_completed
│   ├── feedback/                     # classifier, gap_detector, prioritization,
│   │                                 # deduplication, knowledge_updater
│   ├── integrations/
│   │   ├── github/                   # client, webhooks, app_auth
│   │   ├── slack/                    # client, socket, installation_store, conversation
│   │   ├── discord/                  # client, gateway, interactions, blocks
│   │   ├── clerk/                    # client
│   │   ├── database/                 # client, vector_search, document/jobs/evaluations/
│   │   │                             # and memory_* stores
│   │   └── strands/                  # client, graph, models, tools
│   ├── memory/
│   │   ├── models/                   # knowledge, document, conversation, question,
│   │   │                             # solution, issue, feedback, project
│   │   ├── embeddings.py
│   │   ├── retrieval.py
│   │   ├── ranking.py
│   │   ├── repository.py
│   │   └── service.py
│   ├── models/
│   │   ├── providers/                # bedrock, mantle, openrouter, nvidia,
│   │   │                             # requesty, orcarouter
│   │   ├── registry.py · router.py · factory.py
│   │   └── capabilities · policies · health · embeddings · config
│   ├── observability/                # tracing, metrics, audit, events
│   ├── orchestration/
│   │   ├── graphs/                   # documentation, support, issue, feedback,
│   │   │                             # evaluation
│   │   ├── nodes/                    # base, evaluate
│   │   ├── hooks/                    # review_gate, audit
│   │   ├── routing/                  # classifiers, conditions, policies
│   │   └── state/                    # Per-domain graph state
│   ├── persistence/
│   │   ├── repositories/             # events, jobs, documents, evaluations, reviews,
│   │   │                             # reviewers, memory, organizations, github,
│   │   │                             # slack, discord, support, delivery, agent_runs
│   │   └── migrations/
│   ├── review/                       # queue, approvals, policies, rejection
│   ├── security/                     # webhook_verification, redaction, secrets,
│   │                                 # permissions, audit
│   ├── skills/                       # 20 packaged skills (SKILL.md + references/,
│   │                                 # some with assets/ templates)
│   ├── support/                      # classifier, answer, escalation, resolver
│   ├── tools/
│   │   ├── search/                   # semantic, keyword, hybrid
│   │   ├── github/                   # get_diff/files/issue/pr, create_branch/
│   │   │                             # commit/comment/pull_request
│   │   ├── slack/ · discord/         # get_thread, post_message, search_messages
│   │   ├── repository/               # git, filesystem, code_search
│   │   └── documentation/            # markdown, frontmatter, links, structure
│   ├── workflows/
│   │   ├── documentation/            # sync, audit, github_pr, github_release
│   │   ├── github/                   # issue_resolution, issue_feedback
│   │   ├── support/                  # slack/discord support, resolution
│   │   ├── feedback/                 # feedback loop, prioritization, knowledge update
│   │   ├── evaluation/               # documentation/support evaluation
│   │   └── registry.py · runner.py · state.py · context.py
│   ├── config.py · constants.py · errors.py
│   ├── logging.py · telemetry.py · version.py
├── tests/
│   ├── unit/                         # agents, model providers, routing/hooks
│   ├── graph/                        # Orchestration graphs, review gate
│   ├── workflow/ · workflows/        # PR, issue, support, feedback loop, audit hook
│   ├── domain/                       # evaluation, feedback, memory, review, security
│   ├── evaluation/                   # groundedness, quality, accuracy, runner
│   ├── tools/                        # search, github, repository, documentation
│   ├── nodes/ · conditions/ · agents/ · api/
│   ├── integration/                  # Live DB + evaluator tests (DRAFTLY_LIVE=1)
│   └── fakes/ · stub_model.py · conftest.py
└── workers/
    ├── event_worker.py
    ├── workflow_worker.py
    ├── indexing_worker.py
    └── evaluation_worker.py
```

> [!NOTE]
> `__init__.py` package files are omitted from the tree for readability.

## Getting started

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- A PostgreSQL database (NeonDB works out of the box)

### Install

```bash
uv sync
```

### Configure

```bash
cp .env.example .env
```

Fill in at least `DATABASE_URL` and your AWS credentials (or rely on an IAM role).

> [!TIP]
> Only `DATABASE_URL` and AWS access are required. All other model providers (NVIDIA, OpenRouter, Requesty, Orcarouter, Mantle) are optional fallbacks for the model router.

### Run

```bash
python main.py
```

This starts the FastAPI app on the configured host and port. Interactive API docs are available at `/docs`.

## Background workers

Draftly splits long-running work into dedicated worker processes:

| Worker              | Purpose                                                                                      | Command                                         |
| ------------------- | -------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| `event_worker`      | Serves the API so webhook events are processed by the in-process workflow runner             | `python -m workers.event_worker`                |
| `workflow_worker`   | Runs periodic jobs: workflow retries and review expiry                                       | `python -m workers.workflow_worker`             |
| `indexing_worker`   | Pulls repository content into the documentation store and refreshes the index on an interval | `python -m workers.indexing_worker`             |
| `evaluation_worker` | Runs the evaluation loop once (batch) or continuously (`--watch`, CI mode)                   | `python -m workers.evaluation_worker [--watch]` |

Environment-specific defaults live in [`config/`](config) (`development.yaml`, `staging.yaml`, `production.yaml`).

## Configuration

Key environment variables (see [`.env.example`](.env.example) for the full list):

| Variable                                                                         | Description                                 |
| -------------------------------------------------------------------------------- | ------------------------------------------- |
| `DATABASE_URL`                                                                   | PostgreSQL connection string                |
| `REDIS_URL`                                                                      | Redis connection string (default: `redis://localhost:6379/0`) |
| `SEMANTIC_CACHE_ENABLED`                                                         | Enable LLM semantic cache (default: `True`) |
| `VECTOR_SEARCH_BACKEND`                                                          | `redis`, `pgvector`, or `dual` (default: `dual`) |
| `EVENT_BUS_BACKEND`                                                              | `pubsub`, `stream`, or `dual` (default: `dual`) |
| `AWS_REGION`                                                                     | Region for Amazon Bedrock                   |
| `BEDROCK_CLAUDE_REASONING_MODEL` / `BEDROCK_CLAUDE_FAST_MODEL`                   | Claude model overrides                      |
| `EMBEDDING_MODEL_ID`                                                             | Embedding model for semantic search         |
| `MANTLE_API_KEY` / `MANTLE_ENDPOINT_URL`                                         | Bedrock Mantle (OpenAI-compatible) endpoint |
| `OPENROUTER_API_KEY`, `NVIDIA_API_KEY`, `REQUESTY_API_KEY`, `ORCAROUTER_API_KEY` | Optional additional providers               |

## Testing

```bash
uv run pytest                # unit + workflow tests
uv run pytest -m integration # live integration tests
```

> [!IMPORTANT]
> Integration tests hit a live database and real model endpoints. Set `DRAFTLY_LIVE=1` and provide valid credentials before running them.

### Code quality

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

## Documentation

Design documents live in [`docs/`](docs), covering:

- **Architecture** — system overview & design, event-driven design, multi-agent topology, orchestration graphs, memory, feedback loop, documentation pipeline, persistence, integrations, security, delivery, evaluation, review, observability, support, tools, Redis, and the models router
- **Agents** — agent overview, subagents (Research Swarm), and the 20 packaged skills
- **API** — route reference and webhook handling for GitHub/Slack/Discord/Clerk
- **Workflows** — workflow system overview plus documentation sync, GitHub PR/issue, support, and feedback loop guides
- **Deployment** — AWS, production checklist, CockroachDB, and Redis operations

[`simulation/scenarios/`](simulation/scenarios) contains end-to-end scenarios used to exercise auth flows (OAuth, PKCE), RBAC, token rotation, API key deprecation, and SDK breaking changes against the platform.
