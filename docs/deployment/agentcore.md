# Amazon Bedrock AgentCore deployment

Use this guide to build, deploy, and invoke Draftly's standalone Amazon Bedrock AgentCore Runtime.

## Runtime contract

The `agentcore_server.py` entry point listens on port 8080 and drives the composed Strands workflows. It exposes the two AgentCore Runtime endpoints:

- `GET /ping` — liveness probe.
- `POST /invocations` — accepts `{"input": {"event": {...}}}`, where `event` is a normalized Draftly workflow event with a routable `event_type`. The run ID defaults from the `x-agentcore-session-id` header, which must contain at least 33 characters, when `event.event_id` is absent. The response is `{"output": <run state>}`.

## Deploy

Build the Linux ARM64 image, push it to the `draftly-agentcore` ECR repository, and apply the Terraform configuration:

```bash
make docker-build-agentcore
make docker-push-agentcore
cd infra/aws/terraform
terraform apply
```

The Terraform module provisions the ECR repository, AgentCore runtime IAM role, CloudWatch log group, and runtime. It uses `scripts/deploy_agentcore.py` to create the runtime and configures `OTEL_SERVICE_NAME` and `OTEL_EXPORTER_OTLP_ENDPOINT` in the container.

CloudWatch transaction search is a one-time, per-account setting in **Application Signals > Transaction search**. For full ADOT auto-instrumentation, include the `aws-opentelemetry-distro` package and run:

```bash
opentelemetry-instrument python agentcore_server.py
```

## Invoke the runtime

Replace the ARN placeholder with the runtime ARN produced by your deployment:

```python
import json

import boto3

client = boto3.client("bedrock-agentcore", region_name="us-east-1")
response = client.invoke_agent_runtime(
    agentRuntimeArn=(
        "arn:aws:bedrock-agentcore:us-east-1:<account>:"
        "runtime/draftly-agentcore-suffix"
    ),
    runtimeSessionId="a" * 33,
    payload=json.dumps(
        {"input": {"event": {"event_type": "slack_support"}}}
    ).encode(),
)
print(json.loads(response["response"].read()))
```

For the broader AWS topology and production requirements, see [AWS deployment](aws.md) and [production deployment](production.md).
