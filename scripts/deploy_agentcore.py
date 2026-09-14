#!/usr/bin/env python
"""Provision or update a Draftly AgentCore runtime via the AWS SDK.

Mirrors Strands docs "Method B: Manual Deployment with boto3":
https://strandsagents.com/docs/user-guide/deploy/deploy_to_bedrock_agentcore/python/
Invoked by Terraform local-exec; also usable standalone.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import boto3


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container-uri", required=True)
    parser.add_argument("--role-arn", required=True)
    parser.add_argument("--runtime-name", default="draftly-agentcore")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--network-mode", choices=["PUBLIC", "VPC"], default="PUBLIC")
    parser.add_argument("--security-group-ids", nargs="*", default=[])
    parser.add_argument("--subnet-ids", nargs="*", default=[])
    parser.add_argument("--out", help="path to write JSON with the runtime ARN")
    return parser.parse_args(argv)


def deploy(args: argparse.Namespace) -> dict[str, Any]:
    client = boto3.client("bedrock-agentcore-control", region_name=args.region)

    network_configuration: dict[str, Any] = {"networkMode": args.network_mode}
    if args.network_mode == "VPC":
        network_configuration["networkModeConfig"] = {
            "subnets": args.subnet_ids,
            "securityGroups": args.security_group_ids,
        }

    response = client.create_agent_runtime(
        agentRuntimeName=args.runtime_name,
        agentRuntimeArtifact={
            "containerConfiguration": {"containerUri": args.container_uri}
        },
        networkConfiguration=network_configuration,
        roleArn=args.role_arn,
    )

    result = {
        "agentRuntimeArn": response["agentRuntimeArn"],
        "status": response["status"],
    }
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(result, handle)
    print(json.dumps(result, indent=2))
    return result


def main(argv: list[str] | None = None) -> int:
    deploy(_parse_args(argv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
