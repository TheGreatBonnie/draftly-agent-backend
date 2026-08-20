from typing import Any

from .client import DeepEvalClient, EvaluationRequest


class DeepEvalEvaluations:
    def __init__(self, client: DeepEvalClient):
        self.client = client

    def run(
        self,
        *,
        name: str,
        input: str,
        actual_output: str,
        expected_output: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = EvaluationRequest(
            name=name,
            input=input,
            actual_output=actual_output,
            expected_output=expected_output,
            metadata=metadata,
        )

        return self.client.evaluate(request)
