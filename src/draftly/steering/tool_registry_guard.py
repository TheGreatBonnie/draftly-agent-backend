"""Bound model calls to tools missing from the active Strands registry."""

from strands.interventions import Guide, InterventionHandler, Proceed


class ToolRegistryGuard(InterventionHandler):
    """Correct invented tool names, then fail an invocation that keeps retrying.

    Strands' steering plugin swallows callback exceptions, so the terminal
    failure uses an intervention whose default ``on_error='throw'`` propagates.
    The agent registry is read at call time to include dynamic structured
    output and plugin tools registered for this invocation.
    """

    name = "draftly-tool-registry-guard"

    def __init__(self, *, max_guides: int = 2) -> None:
        self.max_guides = max_guides
        self._unknown_attempts = 0

    def before_invocation(self, event, **kwargs):
        self._unknown_attempts = 0
        return Proceed()

    def before_tool_call(self, event, **kwargs):
        tool_name = event.tool_use["name"]
        available = event.agent.tool_names
        if tool_name in available:
            return Proceed()

        self._unknown_attempts += 1
        names = ", ".join(sorted(available)) or "none"
        if self._unknown_attempts > self.max_guides:
            raise RuntimeError(
                f"Repeated unregistered tool '{tool_name}' after "
                f"{self.max_guides} corrections; available tools: {names}"
            )
        return Guide(
            feedback=(
                f"Tool '{tool_name}' is unregistered. Available tools: {names}. "
                "Use an exact registered name with its required arguments. "
                "If no suitable tool exists, report that limitation."
            )
        )
