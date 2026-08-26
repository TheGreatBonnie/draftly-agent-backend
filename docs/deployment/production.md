# Production Deployment

> **Status:** Implemented
> **Date:** 2026-08-25
> **Scope:** Production readiness checklist, configuration hardening, and operational procedures for running Draftly in production.

## 1. Overview

Running Draftly in production requires attention to security, reliability, and observability. This guide covers the configuration changes, infrastructure decisions, and operational procedures needed to move from development to a production-ready deployment.

```mermaid
flowchart TD
    Dev["Development"] --> Checklist["Production Checklist"]
    Checklist --> Security["Security Hardening"]
    Checklist --> Reliability["Reliability"]
    Checklist --> Observability["Observability"]
    Security --> Ready["Production Ready"]
    Reliability --> Ready
    Observability --> Ready
```

## 2. Production Checklist

### 2.1 Security

| Item | Action | Why |
|------|--------|-----|
| API key authentication | Set `REQUIRE_API_KEY=true` and generate a strong key | Prevents unauthorized API access |
| Webhook secrets | Configure `GITHUB_WEBHOOK_SECRET`, `SLACK_SIGNING_SECRET`, `DISCORD_PUBLIC_KEY` | Validates inbound webhook signatures |
| Environment variables | Never commit `.env` files; use secret managers | Protects credentials |
| CORS | Restrict `FRONTEND_URL` to the production domain | Prevents cross-origin abuse |
| Debug mode | Set `DEBUG=false` | Prevents information leakage |
| Review policy | Set `STRANDS_REVIEW_POLICY=always` for initial rollout | Human oversight on all outputs |

### 2.2 Reliability

| Item | Action | Why |
|------|--------|-----|
| Database backups | Enable NeonDB point-in-time recovery | Data durability |
| Redis persistence | Enable AOF or RAG for Redis data | Queue durability |
| Worker redundancy | Run ≥2 worker instances | No single point of failure |
| Health checks | Configure ALB health check on `/api/health` | Automatic container replacement |
| Timeouts | Tune `STRANDS_EXECUTION_TIMEOUT` and `STRANDS_NODE_TIMEOUT` | Prevents runaway graphs |
| Token budgets | Set `MAX_TOKENS_PER_RUN=50000` | Prevents cost explosions |
| Rate limiting | Set `RATE_LIMITING_ENABLED=true` | Protects against abuse |

### 2.3 Observability

| Item | Action | Why |
|------|--------|-----|
| Structured logging | Set `LOG_LEVEL=INFO` (or `WARNING` in high-traffic) | Machine-parseable logs |
| Metrics | Expose Prometheus metrics from the API | Dashboards and alerting |
| Error tracking | Integrate Sentry or equivalent | Alert on exceptions |
| Queue monitoring | Run RQ Dashboard or equivalent | Visibility into job processing |
| Token usage tracking | Monitor `draftly_tokens_*` metrics | Cost management |

## 3. Configuration Hardening

### 3.1 Strands Runtime

```bash
# Conservative production defaults
STRANDS_MAX_NODE_EXECUTIONS=10
STRANDS_EXECUTION_TIMEOUT=600
STRANDS_NODE_TIMEOUT=180
STRANDS_REVIEW_POLICY=always
MAX_TOKENS_PER_RUN=50000
RECURSION_LIMIT=30
SUMMARIZATION_TRIGGER_FRACTION=0.70
```

**Rationale:**
- `max_node_executions=10` prevents infinite loops in agent graphs.
- `execution_timeout=600` (10 minutes) caps total graph runtime.
- `node_timeout=180` (3 minutes) prevents individual nodes from stalling.
- `review_policy=always` ensures human approval for all deliveries initially. Relax to `risky` once confidence in the system is established.

### 3.2 Worker Configuration

```bash
WORKER_ENABLED=true
WORKER_CONCURRENCY=4
RQ_QUEUE_PREFIX=draftly
RQ_SCHEDULER_ENABLED=true
RQ_WORKER_QUEUES=scheduled,webhooks,default
```

**Scaling guidance:**

| Traffic Level | Concurrency | Worker Instances |
|---------------|-------------|------------------|
| Low (< 100 events/day) | 2 | 1 |
| Medium (100–1000 events/day) | 4 | 2 |
| High (> 1000 events/day) | 8 | 3+ |

### 3.3 Database Pooling

```bash
DATABASE_POOL_MIN_SIZE=2
DATABASE_POOL_MAX_SIZE=10
```

For multi-instance deployments, calculate: `MAX_POOL_SIZE × API_INSTANCES + WORKER_CONCURRENCY × WORKER_INSTANCES` must stay below your database's `max_connections`.

### 3.4 Redis Subsystems

```bash
REDIS_URL=redis://your-redis-host:6379/0
EVENTS_STREAMING_ENABLED=true
SEMANTIC_CACHE_ENABLED=true
VECTOR_SEARCH_BACKEND=dual
EVENT_BUS_BACKEND=dual
RATE_LIMITING_ENABLED=true
API_CACHE_ENABLED=true
```

The `dual` backend mode uses both Redis and PostgreSQL for redundancy. For cost optimization, start with `redis` and upgrade to `dual` as data volume grows.

## 4. Deployment Architecture

### 4.1 Minimal Production Setup

```mermaid
flowchart TD
    subgraph AWS
        ALB["ALB"]
        subgraph ECS/EKS
            API1["API #1"]
            Worker1["Worker #1"]
        end
        subgraph Managed
            NeonDB[("NeonDB")]
            Redis[("Redis")]
        end
    end
    ALB --> API1
    API1 --> NeonDB
    API1 --> Redis
    Worker1 --> NeonDB
    Worker1 --> Redis
```

### 4.2 High-Availability Setup

```mermaid
flowchart TD
    subgraph AWS
        ALB["ALB"]
        subgraph ECS/EKS
            API1["API #1"]
            API2["API #2"]
            Worker1["Worker #1"]
            Worker2["Worker #2"]
            Worker3["Worker #3"]
        end
        subgraph Managed
            NeonDB[("NeonDB HA")]
            Redis[("Redis Cluster")]
        end
    end
    ALB --> API1
    ALB --> API2
    API1 --> NeonDB
    API2 --> NeonDB
    API1 --> Redis
    API2 --> Redis
    Worker1 --> NeonDB
    Worker2 --> NeonDB
    Worker3 --> NeonDB
    Worker1 --> Redis
    Worker2 --> Redis
    Worker3 --> Redis
```

## 5. Operational Procedures

### 5.1 Deployment

```bash
# Build and push images
docker build -f docker/Dockerfile.api -t draftly-api:latest .
docker build -f docker/Dockerfile.worker -t draftly-worker:latest .
docker push ...

# Update ECS service (example)
aws ecs update-service --cluster draftly --service draftly-api --force-new-deployment
aws ecs update-service --cluster draftly --service draftly-worker --force-new-deployment
```

### 5.2 Scaling Workers

```bash
# Scale workers based on queue depth
aws ecs update-service --cluster draftly --service draftly-worker --desired-count 3
```

Monitor `RQ` queue depth and scale workers when pending jobs exceed processing capacity.

### 5.3 Database Maintenance

- NeonDB handles maintenance automatically for serverless plans.
- For dedicated plans, schedule maintenance windows during low-traffic periods.
- Monitor connection count and query latency via NeonDB dashboard.

### 5.4 Log Management

Structured logs use `structlog` and output JSON. Key log events:

| Event | Level | Meaning |
|-------|-------|---------|
| `runner_duplicate` | INFO | Event was already processed |
| `pr_workflow_done` | INFO | PR workflow completed |
| `slack_support_done` | INFO | Slack support workflow completed |
| `documentation_sync_done` | INFO | Doc sync completed |
| `runner_claim_failed` | ERROR | Idempotency claim failed |
| `runner_publish_failed` | WARNING | Streaming publish failed (non-fatal) |

### 5.5 Rollback

1. Tag the current image version before deployment.
2. If issues arise, update the ECS service to use the previous image tag.
3. Database migrations are backward-compatible by design; no rollback needed.

## 6. Cost Management

| Cost Driver | Control Mechanism |
|-------------|-------------------|
| LLM token usage | `MAX_TOKENS_PER_RUN`, `SUMMARIZATION_TRIGGER_FRACTION` |
| Worker compute | `WORKER_CONCURRENCY`, horizontal scaling |
| Redis memory | `SEMANTIC_CACHE_ENABLED`, TTL on cached items |
| Database storage | NeonDB auto-scaling; monitor via dashboard |
| Network | ALB request routing; minimize cross-AZ traffic |

## 7. File Reference

- `docker/Dockerfile` — Base image
- `docker/Dockerfile.api` — API image (port 8000)
- `docker/Dockerfile.worker` — Worker image (background processing)
- `src/draftly/app/config.py` — All configuration with defaults
- `src/draftly/workflows/runner.py` — WorkflowRunner (execution engine)
- `src/draftly/workflows/context.py` — WorkflowContext (dependency bundle)
- `src/draftly/observability/metrics.py` — Prometheus metrics
