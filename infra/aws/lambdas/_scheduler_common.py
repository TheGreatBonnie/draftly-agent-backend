"""Shared ECS run-task trigger for the Draftly scheduler lambdas."""

from __future__ import annotations

import os
from typing import Any

import boto3


def run_worker(event: dict[str, Any]) -> dict[str, Any]:
    region = os.environ.get("AWS_REGION", "us-east-1")
    cluster = os.environ["CLUSTER_NAME"]
    task_definition = os.environ["TASK_DEFINITION"]
    container = os.environ["CONTAINER_NAME"]
    security_group = os.environ["SECURITY_GROUP_ID"]
    subnets = [s for s in os.environ.get("SUBNET_IDS", "").split(",") if s]

    kwargs: dict[str, Any] = {}
    worker_type = event.get("worker_type") or os.environ.get("WORKER_TYPE", "")
    if worker_type:
        kwargs["overrides"] = {
            "containerOverrides": [
                {
                    "name": container,
                    "environment": [{"name": "WORKER_TYPE", "value": worker_type}],
                }
            ]
        }

    ecs = boto3.client("ecs", region_name=region)
    response = ecs.run_task(
        cluster=cluster,
        taskDefinition=task_definition,
        launchType="FARGATE",
        count=1,
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": subnets,
                "securityGroups": [security_group],
                "assignPublicIp": "DISABLED",
            }
        },
        **kwargs,
    )
    return {"tasks": response.get("tasks", []), "failures": response.get("failures", [])}