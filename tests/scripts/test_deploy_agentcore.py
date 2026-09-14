from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch


def _load_deploy_script() -> Any:
    """Load scripts/deploy_agentcore.py as a module (repo convention)."""
    script = Path(__file__).resolve().parents[2] / "scripts" / "deploy_agentcore.py"
    spec = importlib.util.spec_from_file_location("deploy_agentcore_under_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DEPLOY_MODULE = _load_deploy_script()
deploy = DEPLOY_MODULE.deploy


def _fake_boto3(client: MagicMock) -> MagicMock:
    fake = MagicMock()
    fake.client.return_value = client
    return fake


def test_deploy_creates_public_runtime() -> None:
    client = MagicMock()
    client.create_agent_runtime.return_value = {
        "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:123:runtime/draftly",
        "status": "CREATING",
    }
    args = MagicMock(
        container_uri="123.dkr.ecr.us-east-1.amazonaws.com/draftly-agentcore:latest",
        role_arn="arn:aws:iam::123:role/draftly-agentcore-runtime",
        runtime_name="draftly-agentcore",
        region="us-east-1",
        network_mode="PUBLIC",
        security_group_ids=[],
        subnet_ids=[],
        env=[],
        out=None,
    )
    with patch.object(DEPLOY_MODULE, "boto3", _fake_boto3(client)):
        result = deploy(args)

    assert result["status"] == "CREATING"
    client.create_agent_runtime.assert_called_once_with(
        agentRuntimeName="draftly-agentcore",
        agentRuntimeArtifact={
            "containerConfiguration": {"containerUri": args.container_uri}
        },
        networkConfiguration={"networkMode": "PUBLIC"},
        roleArn=args.role_arn,
    )


def test_deploy_vpc_mode_passes_subnets_and_security_groups() -> None:
    client = MagicMock()
    client.create_agent_runtime.return_value = {
        "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:123:runtime/draftly",
        "status": "CREATING",
    }
    args = MagicMock(
        container_uri="123.dkr.ecr.us-east-1.amazonaws.com/draftly-agentcore:latest",
        role_arn="arn:aws:iam::123:role/draftly-agentcore-runtime",
        runtime_name="draftly-agentcore",
        region="us-east-1",
        network_mode="VPC",
        security_group_ids=["sg-1"],
        subnet_ids=["subnet-1"],
        env=[],
        out=None,
    )
    with patch.object(DEPLOY_MODULE, "boto3", _fake_boto3(client)):
        deploy(args)

    _, kwargs = client.create_agent_runtime.call_args
    assert kwargs["networkConfiguration"] == {
        "networkMode": "VPC",
        "networkModeConfig": {"subnets": ["subnet-1"], "securityGroups": ["sg-1"]},
    }


def test_deploy_writes_out_file() -> None:
    client = MagicMock()
    client.create_agent_runtime.return_value = {
        "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:123:runtime/draftly",
        "status": "CREATING",
    }
    args = MagicMock(
        container_uri="u", role_arn="r", runtime_name="draftly-agentcore",
        region="us-east-1", network_mode="PUBLIC",
        security_group_ids=[], subnet_ids=[], env=[], out="/tmp/agentcore-runtime.json",
    )
    with patch.object(DEPLOY_MODULE, "boto3", _fake_boto3(client)), \
         patch.object(DEPLOY_MODULE, "open", MagicMock()) as open_mock:
        deploy(args)
        open_mock.assert_called_once_with(
            "/tmp/agentcore-runtime.json", "w", encoding="utf-8"
        )


def test_deploy_passes_env_as_environment_variables() -> None:
    client = MagicMock()
    client.create_agent_runtime.return_value = {
        "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:123:runtime/draftly",
        "status": "CREATING",
    }
    args = MagicMock(
        container_uri="u",
        role_arn="r",
        runtime_name="draftly-agentcore",
        region="us-east-1",
        network_mode="PUBLIC",
        security_group_ids=[],
        subnet_ids=[],
        env=["DATABASE_URL=postgres://db", "OTEL_SERVICE_NAME=draftly-agentcore"],
        out=None,
    )
    with patch.object(DEPLOY_MODULE, "boto3", _fake_boto3(client)):
        deploy(args)

    _, kwargs = client.create_agent_runtime.call_args
    assert kwargs["environmentVariables"] == {
        "DATABASE_URL": "postgres://db",
        "OTEL_SERVICE_NAME": "draftly-agentcore",
    }


def test_deploy_omits_environment_variables_when_empty() -> None:
    client = MagicMock()
    client.create_agent_runtime.return_value = {
        "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:123:runtime/draftly",
        "status": "CREATING",
    }
    args = MagicMock(
        container_uri="u",
        role_arn="r",
        runtime_name="draftly-agentcore",
        region="us-east-1",
        network_mode="PUBLIC",
        security_group_ids=[],
        subnet_ids=[],
        env=[],
        out=None,
    )
    with patch.object(DEPLOY_MODULE, "boto3", _fake_boto3(client)):
        deploy(args)

    _, kwargs = client.create_agent_runtime.call_args
    assert "environmentVariables" not in kwargs


def test_parse_args_accepts_env_pairs() -> None:
    args = DEPLOY_MODULE._parse_args(
        [
            "--container-uri", "u",
            "--role-arn", "r",
            "--env", "DATABASE_URL=postgres://db",
            "--env", "OTEL_EXPORTER_OTLP_ENDPOINT=http://otel:4317",
        ]
    )
    assert args.env == [
        "DATABASE_URL=postgres://db",
        "OTEL_EXPORTER_OTLP_ENDPOINT=http://otel:4317",
    ]
