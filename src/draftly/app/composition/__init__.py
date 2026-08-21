"""
Draftly application composition root.

The composition package is responsible for wiring together
Draftly's runtime components:

- agents
- tools
- workflows
- events

This package should contain dependency wiring only.

Business logic belongs in domain/, workflows/, agents/, tools/,
or integrations/.
"""

from .agents import AgentRegistry, build_agents
from .events import EventComposition, build_event_system
from .tools import ToolRegistry, build_tools
from .workflows import ComposedWorkflows, build_workflows

__all__ = [
    "AgentRegistry",
    "ComposedWorkflows",
    "EventComposition",
    "ToolRegistry",
    "build_agents",
    "build_event_system",
    "build_tools",
    "build_workflows",
]
