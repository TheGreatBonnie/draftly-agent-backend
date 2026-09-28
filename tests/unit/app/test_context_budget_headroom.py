"""The context turn budget must exceed the agent's real evidence demand.

Run ``02b58350`` (PR #63) was the first run with the budget applied. The
context node took exactly 12 model calls -- the configured cap -- and was cut
off mid-sweep:

===================  ===============  =========================
run                 model calls      tool calls
===================  ===============  =========================
``ce8ea540``        died mid-flight  11 (incomplete)
``02b58350``        12 (capped)      14, still gathering
===================  ===============  =========================

It never got the turn it needed to emit the ``EvidenceBundle``, which surfaced
as ``memory_grounded_no_structured_output`` -- the node finished "successfully"
with zero evidence. The cap was below the demand it was meant to bound, so it
did not bound the loop, it amputated the result.
"""

from __future__ import annotations

from draftly.app.config import Settings, StrandsConfig

#: One ``Limits`` cycle is one model call plus the tool execution that follows,
#: so ``after_model`` steering events count cycles exactly.
MEASURED_MAX_CYCLES = 12

#: The agent was still calling ``github_read_file`` when the cap fired, so 12
#: understates demand. The sweep also needs one further cycle to emit the
#: structured-output tool call.
MINIMUM_USEFUL_CYCLES = 20


def test_the_cap_is_above_the_measured_demand() -> None:
    """The bug: a cap at or below demand amputates the evidence bundle."""
    assert StrandsConfig().context_max_turns > MEASURED_MAX_CYCLES


def test_the_cap_leaves_room_to_emit_the_bundle() -> None:
    """Evidence gathering plus a final structured-output cycle."""
    assert StrandsConfig().context_max_turns >= MINIMUM_USEFUL_CYCLES


def test_the_cap_is_still_a_real_bound() -> None:
    """The point of the budget: an unbounded agent decided how long a run ran.

    ``ce8ea540``'s context node spent 84.9s before dying on an unrelated
    truncation. The cap must stay well under "no ceiling" or fix 4 has been
    undone in the name of fixing its own regression.
    """
    assert StrandsConfig().context_max_turns <= 60


def test_the_default_still_comes_from_settings() -> None:
    assert Settings().strands.context_max_turns == StrandsConfig().context_max_turns
