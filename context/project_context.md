# Draftly — Project Context

Draftly is an autonomous documentation-engineering platform. It watches GitHub
PRs, issues, and releases plus Slack/Discord support channels, and keeps the
project's documentation accurate and complete.

## Mission

One documentation-intelligence engine with three event surfaces:

- GitHub Pull Request / Issue / Release
- Slack support
- Discord support

All converge on one pipeline:

```
ingest -> classify -> context -> research (swarm) -> impact
      -> generate (answer/update/create) -> evaluate (revise loop)
      -> human review (interrupt) -> deliver
```

## Outputs

- Documentation pull requests on GitHub
- Answers posted back in Slack/Discord threads
- Documentation gap reports driving future documentation work

## Principles

- Graph = governance. Swarm = exploration.
- Every claim is grounded in evidence.
- Nothing is delivered without evaluation; risky changes are human-reviewed.
- Support questions close the loop into documentation improvements.