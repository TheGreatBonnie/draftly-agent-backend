"""Production ``Agent(...)`` construction is fully centralized.

Task 10 coverage regression: after the migration every production ``Agent(``
site must live in the centralized constructor (``agents/factory.py``) or the
explicitly isolated judge boundary (``steering/handler.py``). Any third site
(null handlers, bypassed factories, raw onboarding-stage agents) fails here so
steering cannot be silently skipped.
"""

from __future__ import annotations

from pathlib import Path

SOURCE_ROOT = Path("src/draftly")

#: The only production modules permitted to open raw ``Agent(...)``.
ALLOWED_CONSTRUCTION_SITES = {
    "src/draftly/agents/factory.py",
    "src/draftly/steering/handler.py",
}


def _construction_sites() -> set[str]:
    sites: set[str] = set()
    for path in SOURCE_ROOT.rglob("*.py"):
        if "Agent(" in path.read_text():
            sites.add(str(path))
    return sites


def test_agent_constructions_are_contained_to_allowlist() -> None:
    sites = _construction_sites()
    assert sites == ALLOWED_CONSTRUCTION_SITES, (
        "raw Agent(...) sites outside the centralized constructor/judge "
        f"boundary: {sorted(sites - ALLOWED_CONSTRUCTION_SITES)}"
    )


def test_allowlist_is_not_vacuous() -> None:
    for site in ALLOWED_CONSTRUCTION_SITES:
        assert "Agent(" in Path(site).read_text(), site


def test_onboarding_stage_agents_use_the_constructor() -> None:
    from draftly.workflows.onboarding import stages

    source = Path(stages.__file__).read_text()
    assert "Agent(" not in source, (
        "onboarding stages must build agents through build_draftly_agent"
    )
