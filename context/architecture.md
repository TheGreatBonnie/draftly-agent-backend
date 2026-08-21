# Draftly — Architecture

High-level architecture of the Draftly codebase.

## Layers

```
events          webhook / Slack / Discord ingestion -> normalized domain events
orchestration   state, conditions, routing policies, and the Strands graphs
agents          LLM agents: classifier, research swarm, writers, reviewers
tools           repository, documentation, search, github, slack, discord tools
workflows       job workflow definitions (documentation, support, github, evaluation)
integrations    strands client, database, github, slack, discord, memory, deepeval
persistence     repositories: events, documents, memory, evaluations, reviews, delivery
app             FastAPI app, composition (tools/agents/workflows/events), lifecycle
workers         task workers for asynchronous job execution
observability   audit logging, tracing, and event telemetry
```

## Key Components

- **GraphBuilder / Strands graph**: the main documentation graph with classify,
  context, research, impact, answer/update/create, evaluate, revise, deliver.
- **Research swarm**: 4 cooperating agents (github, slack, discord, docs) that
  gather evidence with handoffs.
- **ModelRouter**: maps model configs (light/standard/strong/code) to concrete
  Strands model instances.
- **ToolRegistry**: scoped tool groups (documentation, documentation_engineer,
  documentation_reviewer, github_intelligence, support_engineer,
  support_reviewer, research, deepeval, github_delivery, memory_curator,
  slack_*, discord_*, semantic/keyword/hybrid search).

## Delivery

- Documentation changes are delivered as PRs via the GitHub API.
- Support answers are posted back in the originating Slack/Discord thread.
- All deliveries pass the review gate (human review for risky changes).