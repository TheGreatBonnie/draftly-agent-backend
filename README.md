<!-- prettier-ignore -->
<div align="center">

# Draftly

**Documentation that keeps up with your code.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB.svg?style=flat-square)](https://www.python.org/)
[![Strands Agents](https://img.shields.io/badge/Strands_Agents-SDK-0891b2.svg?style=flat-square)](https://github.com/strands-agents/sdk-python)
[![Hackathon](https://img.shields.io/badge/Hackathon-Agents%20for%20Humans-blueviolet?style=flat-square)](https://agentsforhumans.devpost.com/)

[Run locally](#run-draftly) · [Walkthrough](#from-code-change-to-reviewed-documentation) · [Architecture](#architecture) · [Frontend](../draftly-agent-ui/README.md)

</div>

Draftly helps SDK maintainers and developer teams keep documentation aligned with their code. It watches GitHub changes and developer questions, researches the project, and prepares documentation updates for review — so maintainers can focus on decisions instead of repeatedly investigating and rewriting docs.

Built with **Strands Agents SDK**, a Python/FastAPI backend, and a Next.js review workspace. This directory contains the backend; the [frontend](../draftly-agent-ui/README.md) provides onboarding, workflow inspection, and human review.

<div align="center">
  <a href="https://youtu.be/o-_z05If_-Y">
    <img src="assets/draftly-thumbnail.png" alt="Watch the Draftly demo" width="720" />
  </a>
</div>

## Architecture

```mermaid
flowchart TB
    %% ═══ ① Event & Input layer ═══
    subgraph L1["① Events & Input"]
        GH["GitHub Webhooks<br/>PR · Issue · Release"]
        SL["Slack Events<br/>Socket Mode"]
        DC["Discord Gateway"]
        CRON["Scheduled Jobs<br/>rq-scheduler"]
    end

    %% ═══ ② Ingestion & Execution layer ═══
    subgraph L2["② Ingestion & Execution"]
        API["FastAPI Routes<br/>auth · webhook verification"]
        EVT["Event Composition<br/>→ normalized envelope → dispatch"]
        RQ["Redis RQ Queues<br/>webhooks · scheduled · default"]
        WRK["RQ Worker"]
        RUN["WorkflowRunner<br/>claim / idempotency · session resume"]
    end

    %% ═══ ③ Strands Orchestration layer (the star) ═══
    subgraph L3["③ Strands Orchestration &mdash; documentation graph"]
        subgraph G["Strands Graph · GraphBuilder · conditional edges"]
            direction TB
            CL["classify"]
            CX["context"]
            RS["research<br/>(Strands Swarm)"]
            IM["impact"]
            UP["update"]
            CR["create"]
            AN["answer"]
            EVAL["evaluate<br/>(EvaluatorNode)"]
            CH["changelog"]
            CE["changelog_evaluate"]
            DE["deliver"]
            NT["notify"]
            NP["notify_post"]
        end

        subgraph XC["Cross-cutting Strands plugins on every agent"]
            STEER["Steering Handler<br/>Guide · Interrupt · Proceed"]
            SKILLS["AgentSkills<br/>22 bundled skills"]
        end

        ROUTE["Model Router<br/>RoleAwareModelResolver"]
        SESS["Session Manager<br/>interrupt resume"]
    end

    %% ═══ ④ Persistence & Integrations layer ═══
    subgraph L4["④ Persistence & Integrations"]
        DB[("PostgreSQL · pgvector<br/>reviews · evaluations · memory · events")]
        REDIS[("Redis<br/>queues · streams · cache")]
        EXT["GitHub · Slack · Discord APIs<br/>delivery + data"]
    end

    %% ═══ ⑤ Human layer ═══
    subgraph L5["⑤ Human layer"]
        PUI["Review Workspace<br/>Next.js"]
        AUTH["Clerk<br/>authentication"]
    end

    %% Flow: events → ingestion
    GH --> API
    SL --> API
    DC --> API
    CRON --> RQ
    API --> EVT
    EVT --> RQ
    RQ --> WRK
    WRK --> RUN

    %% Flow: execution → graph
    RUN --> CL

    %% The documentation graph
    CL --> CX --> RS --> IM
    IM --> AN
    IM --> UP
    IM --> CR
    AN --> EVAL
    UP --> EVAL
    CR --> EVAL
    EVAL -->|passed| CH
    CH --> CE
    CE -->|passed + sealed drafts| DE
    EVAL -->|"needs revision (of writer)"| AN
    EVAL -->|"needs revision (of writer)"| UP
    EVAL -->|"needs revision (of writer)"| CR
    CE -->|needs revision| CH
    IM -->|release · no docs change| CH
    IM -->|PR opened · parallel branch| NT
    NT --> NP

    %% Plugins annotate the graph
    STEER -. "on every tool call / model turn" .-> CL
    SKILLS -. "bundled into each agent" .-> CL

    %% Cross-cutting services feed the graph
    ROUTE -. "role-based model selection" .-> CL
    SESS -.-> RUN

    %% Delivery + persistence + human
    DE -. "open PR / post response" .-> EXT
    DE == "pause: ReviewGate interrupt" ==> PUI
    PUI == "approve / reject · resume" ==> DE
    AUTH -. "sessions" .-> PUI
    RUN -- "SSE live progress + states" --> PUI
    L3 -. "memory grounding · evidence · eval results" .-> DB
    RUN -- "persist runs · reviews · evals" --> DB
    API -- "state + event streams" --> REDIS

    %% ── classes ──
    classDef strands fill:#ecfeff,stroke:#0891b2,color:#164e63,stroke-width:2px;
    classDef draftly fill:#eff6ff,stroke:#2563eb,color:#1e3a8a,stroke-width:1px;
    classDef data fill:#f5f3ff,stroke:#7c3aed,color:#4c1d95,stroke-width:1px;
    classDef external fill:#f8fafc,stroke:#64748b,color:#334155,stroke-width:1px;
    classDef human fill:#fffbeb,stroke:#d97706,color:#78350f,stroke-width:1px;
    classDef note fill:#f0fdf4,stroke:#16a34a,color:#14532d,stroke-width:1px;

    class API,EVT,WRK,RUN,EVAL,NT,NP draftly;
    class CL,CX,RS,IM,UP,CR,AN,CH,CE,STEER,SKILLS,ROUTE,SESS strands;
    class DB,REDIS data;
    class GH,SL,DC,CRON,EXT external;
    class PUI,AUTH human;
```

For deeper implementation detail, see [orchestration](docs/architecture/orchestration.md), [model routing](docs/architecture/models-router.md), [memory](docs/architecture/memory.md), and [delivery](docs/architecture/delivery.md).

## Features

- **Event-triggered documentation maintenance** — starts from merged PRs and release events
- **Grounded developer support** — answers GitHub, Slack, and Discord questions with project context
- **Research with evidence** — connects proposed answers and updates to repository and documentation sources
- **Configurable human review** — `always`, `risky`, or `never` review policies gate delivery
- **Feedback and project memory** — curates knowledge candidates and provides curation and retrieval workflows
- **Agent observability** — inspect agent catalog, runs, and steps via authenticated API

## Run Draftly

### Prerequisites

> [!IMPORTANT]
> You need Python 3.11+, `uv`, Docker for Redis, and a PostgreSQL database with the `vector` extension. Node.js and Clerk configuration are required for the frontend experience.

### 1. Install

From the workspace containing both app directories:

```bash
cd draftly-agent-backend
uv sync --extra dev
cp .env.example .env
```

The `dev` extra installs the linting, type-checking, and test tools used later in this guide. For a runtime-only environment, use `uv sync` instead.

Configure `.env` using the table below. The example file does not list every integration setting; [Settings](src/draftly/app/config.py) is the source for those names.

| Setting                                                           | Required for                                                                                            |
| ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `DATABASE_URL`                                                    | Database connectivity and migrations; use a dedicated development database                              |
| `AWS_REGION` plus AWS credentials or an IAM role                  | The Bedrock model path; alternatively configure a supported model provider                              |
| `REQUESTY_API_KEY`, `ORCAROUTER_API_KEY`, or `OPENROUTER_API_KEY` | At least one embedding provider for semantic retrieval; Bedrock embeddings are not implemented directly |
| `EMBEDDING_MODEL_ID=text-embedding-3-small`                       | The configured embedding provider must serve the model and return 1,536-dimensional vectors             |
| `REDIS_URL=redis://localhost:6379/0`                              | Queue consumption, state, and event streaming                                                           |
| `DEBUG=false`                                                     | Boolean debug setting; an exported shell value overrides `.env`                                         |
| `RQ_ENABLED=true`                                                 | The recommended separate-worker execution path                                                          |
| `VECTOR_SEARCH_BACKEND=pgvector`                                  | Vector search for this setup, because Compose uses plain Redis                                          |
| `SEMANTIC_CACHE_ENABLED=false`                                    | Avoids requiring RediSearch for the semantic cache in this setup                                        |

> [!TIP]
> If your shell exports a non-boolean `DEBUG` value, run `export DEBUG=false` before starting the commands below.

### 2. Initialize the database and start Redis

```bash
uv run python scripts/bootstrap.py
docker compose -f docker-compose.redis.yml up -d redis
docker compose -f docker-compose.redis.yml exec redis redis-cli ping
```

The Redis check should return `PONG`. The bootstrap script applies SQL migrations in filename order and checks connectivity. It handles some duplicate-object errors by skipping; it is not a version-tracking migration system.

### 3. Start both native processes

In one terminal:

```bash
uv run python -m workers.rq_worker
```

In a second terminal:

```bash
uv run python main.py
```

Both processes load the same `.env`. The RQ worker consumes the `scheduled`, `webhooks`, and `default` queues under the configured prefix. Starting only the API does not consume queued jobs.

### 4. Check liveness and readiness

```bash
curl --fail http://localhost:8000/api/health
curl --fail http://localhost:8000/api/health/ready
```

The liveness response is `{"status":"ok","service":"draftly"}`. Readiness returns HTTP 200 only when the database, memory, and evaluation dependencies are available. Explore the API at [localhost:8000/docs](http://localhost:8000/docs).

### 5. Connect the review workspace

Follow the [frontend setup guide](../draftly-agent-ui/README.md), using `API_URL=http://localhost:8000`. Configure `CLERK_PUBLISHABLE_KEY` and `CLERK_SECRET_KEY` on the backend for the same Clerk application used by the frontend.

For the GitHub workflow, configure `GITHUB_APP_ID`, `GITHUB_APP_SLUG`, `GITHUB_PRIVATE_KEY_PATH`, and `GITHUB_WEBHOOK_SECRET`. Install the GitHub App on the target repository and configure its webhook against your backend. See [webhooks](docs/api/webhooks.md) and [GitHub workflow behavior](docs/workflows/github-pr.md).

> [!NOTE]
> Slack and Discord credentials are needed only when connecting those integrations. See [integration architecture](docs/architecture/integrations.md).

## From code change to reviewed documentation

**Illustrative walkthrough: adding OAuth authentication to Authly.** Authly is a fictional benchmark application used to exercise documentation drift. This example follows the checked-in `oauth-authentication-add` evaluation case.

> [!NOTE]
> This walkthrough is illustrative; dataset expectations are not evidence of a successful execution. See the [evidence audit](docs/readme-evidence-audit.md) for what was verified.

1. **A change arrives.** The case represents a merged PR adding OAuth authorization URLs, authorization-code exchange, and OAuth-backed login. The production GitHub route skips PR events that are not merged.
2. **Draftly researches the impact.** Agents inspect the repository and documentation, including the OAuth implementation, authentication flow, and client configuration.
3. **A documentation update is proposed.** The expected update explains provider setup, URL construction, callback handling, code exchange, and login. Writers produce a structured file-change plan; delivery tools are excluded from their tool set.
4. **The output is checked.** The documentation graph evaluates the output and can route it back for revision. It also authors and checks a changelog before reaching delivery.
5. **A maintainer decides.** With `review_policy: always`, the graph pauses before delivery. The reviewer can approve or reject; a rejection cancels delivery.
6. **Delivery follows approval.** The delivery agent has tools to create the documentation PR.

| Before                                     | Expected documentation coverage                           |
| ------------------------------------------ | --------------------------------------------------------- |
| OAuth behavior introduced without guidance | Authorization URL construction and provider configuration |
| Callback handling undocumented             | Callback handling and authorization-code exchange steps   |
| Developers lack guidance for new login     | OAuth-backed login usage                                  |

Inspect the [case and evidence references](src/draftly/evaluation/datasets/documentation.json), [GitHub event handling](src/draftly/app/api/routes/github.py), and [documentation graph](src/draftly/orchestration/graphs/documentation_graph.py).

## Built with Strands Agents

Strands supplies agents, graph orchestration, and interrupt hooks. Draftly supplies the domain tools, routing conditions, review policy, persistence, and delivery integration.

| Responsibility                | Implementation                                                                                                       | Why it matters                                                                                   |
| ----------------------------- | -------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| Research project evidence     | [Shared researchers](src/draftly/agents/shared/research.py)                                                          | Strands agents receive tools and source-specific research instructions                           |
| Coordinate documentation work | [Documentation graph](src/draftly/orchestration/graphs/documentation_graph.py)                                       | `GraphBuilder` connects research, impact analysis, writing, evaluation, and conditional revision |
| Check outputs                 | [Runtime evaluator](src/draftly/orchestration/nodes/evaluate.py) and [evaluation framework](src/draftly/evaluation/) | Runtime quality checks and dataset-based evaluation serve different purposes                     |
| Pause for a person            | [Review gate](src/draftly/orchestration/hooks/review_gate.py)                                                        | A Strands `BeforeNodeCallEvent` interrupt prevents delivery until the required decision          |
| Deliver the result            | [Delivery agent](src/draftly/agents/shared/delivery.py)                                                              | A separate agent receives publication tools after the graph's review boundary                    |

The graph supports `always`, `risky`, and `never` review policies. Unknown policy values resolve to `always`. Review approval does not merge the resulting GitHub PR; maintainers still control that repository action.

## Development and documentation

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -m "not integration"
```

> [!NOTE]
> Live integration tests use external services. Set `DRAFTLY_LIVE=1` before running `uv run pytest -m integration`. These commands are developer entry points, not a claim that this documentation change ran the application test suite.

| Area                        | Entry point                                                                           |
| --------------------------- | ------------------------------------------------------------------------------------- |
| Agents, prompts, and skills | [Agents](src/draftly/agents/) · [packaged skills](src/draftly/skills/)                |
| Graphs and execution        | [Orchestration](src/draftly/orchestration/) · [workflows](src/draftly/workflows/)     |
| API and integrations        | [API routes](src/draftly/app/api/routes/) · [integrations](src/draftly/integrations/) |
| Quality and retrieval       | [Evaluation](src/draftly/evaluation/) · [memory](src/draftly/memory/)                 |
| Frontend                    | [Next.js workspace](../draftly-agent-ui/README.md)                                    |

Continue with the [architecture overview](docs/architecture/overview.md), [workflow guides](docs/workflows/overview.md), [API reference](docs/api/routes.md), [Redis operations](docs/deployment/redis.md), and [production deployment](docs/deployment/production.md).

## Operations

Keep environment-specific procedures outside this entry-point README:

- [Deploy to Amazon Bedrock AgentCore Runtime](docs/deployment/agentcore.md)
- [Run, rebuild, and troubleshoot Redis and RQ workers](docs/deployment/redis.md#11-compose-and-rq-worker-alternatives)
- [Prepare a production deployment](docs/deployment/production.md)

## Resources

- [Strands Agents SDK](https://github.com/strands-agents/sdk-python)
- [Architecture overview](docs/architecture/overview.md)
- [Workflow guides](docs/workflows/overview.md)
- [API reference](docs/api/routes.md)
- [Agents for Humans hackathon](https://agentsforhumans.devpost.com/)
