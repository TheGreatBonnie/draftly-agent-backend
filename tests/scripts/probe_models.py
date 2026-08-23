"""Live model sweep: probe every registered Draftly model against its provider.

Usage:
    uv run python tests/scripts/probe_models.py [--only SUBSTR] [--embeddings] [--out PATH]

Probes each chat model with capability-matched checks (basic completion,
tool invocation asserted via real toolUse blocks, structured output) and
optionally exercises every registered embedding model. Prints a matrix
ranked by probes-passed then total latency and writes raw JSON results.

Requires live provider keys in .env. Nothing is written to the repo.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND / "src"))
load_dotenv(BACKEND / ".env")

PROBE_TIMEOUT_S = 150.0


@dataclass
class ProbeResult:
    label: str
    outcome: str  # "ok" | error class name | "TIMEOUT"
    latency_s: float = 0.0
    detail: str = ""


@dataclass
class ModelResult:
    model_name: str
    provider: str
    priority: int
    capabilities: tuple[str, ...]
    probes: list[ProbeResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for p in self.probes if p.outcome == "ok")

    @property
    def total_latency(self) -> float:
        return sum(p.latency_s for p in self.probes if p.outcome == "ok")


def rank_results(results: list[ModelResult]) -> list[ModelResult]:
    """Best first: most probes passed, then lowest summed latency."""
    return sorted(results, key=lambda r: (-r.passed, r.total_latency))


def run_probe(agent_fn, label: str) -> ProbeResult:
    start = time.monotonic()
    try:
        out = agent_fn()
        return ProbeResult(label, "ok", time.monotonic() - start, str(out)[:80])
    except Exception as exc:  # noqa: BLE001 - the sweep reports any failure
        return ProbeResult(
            label,
            type(exc).__name__,
            time.monotonic() - start,
            str(exc)[:160],
        )


def _tool_was_used(agent) -> bool:
    return any(
        "toolUse" in str(block)
        for msg in agent.messages
        for block in msg.get("content", [])
    )


def probe_model(router, cfg) -> ModelResult:
    from strands import Agent, tool

    @tool
    def roll_dice() -> str:
        """Roll a six-sided die and return the result as a string."""
        return "4"

    class ProbeAnswer(BaseModel):
        city: str
        population_millions: float

    result = ModelResult(cfg.name, cfg.provider, cfg.priority, cfg.capabilities)

    try:
        provider = router.registry.get_provider(cfg.provider)
        agent = Agent(
            model=provider.create_model(cfg),
            tools=[roll_dice],
            callback_handler=None,
        )
    except Exception as exc:  # noqa: BLE001
        result.probes.append(
            ProbeResult("instantiate", type(exc).__name__, 0.0, str(exc)[:160])
        )
        return result

    result.probes.append(
        run_probe(lambda: agent("Reply with exactly: pong"), "basic")
    )

    if "tool_calling" in cfg.capabilities:

        def tool_probe():
            agent("Use the roll_dice tool and tell me what it returned.")
            if not _tool_was_used(agent):
                raise AssertionError("model never invoked the tool")
            return "tool used"

        result.probes.append(run_probe(tool_probe, "tool_call"))

    if "structured_output" in cfg.capabilities:
        result.probes.append(
            run_probe(
                lambda: agent.structured_output(
                    ProbeAnswer,
                    "What city has roughly 8.5 million people? Fill the schema.",
                ),
                "structured",
            )
        )

    return result


def probe_embeddings() -> list[ModelResult]:
    from draftly.models.factory import build_embedding_router

    router = build_embedding_router()
    results: list[ModelResult] = []

    for cfg in router.registry.list_embedding_models():
        result = ModelResult(cfg.name, cfg.provider, cfg.priority, ("embedding",))
        try:
            provider = router.registry.get_provider(cfg.provider)
            embedder = provider.create_embedder(cfg)
        except Exception as exc:  # noqa: BLE001
            result.probes.append(
                ProbeResult("instantiate", type(exc).__name__, 0.0, str(exc)[:160])
            )
            results.append(result)
            continue

        result.probes.append(
            run_probe(lambda: embedder.embed_query("ping"), "embed")
        )
        results.append(result)

    return results


def sweep(router, only: str | None) -> list[ModelResult]:
    models = router.registry.list_models()
    if only:
        models = [m for m in models if only.lower() in m.name]

    print(f"probing {len(models)} chat models...")

    results: list[ModelResult] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(probe_model, router, cfg): cfg.name for cfg in models}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                res = fut.result(timeout=PROBE_TIMEOUT_S * 3)
            except TimeoutError:
                res = ModelResult(name, "?", 0, ())
                res.probes.append(ProbeResult("sweep", "TIMEOUT"))
            except Exception as exc:  # noqa: BLE001
                res = ModelResult(name, "?", 0, ())
                res.probes.append(
                    ProbeResult("sweep", type(exc).__name__, 0.0, str(exc)[:120])
                )
            results.append(res)
            status = ",".join(f"{p.label}:{p.outcome}" for p in res.probes)
            print(f"[done] {res.model_name} -> {status}", flush=True)

    return results


def print_report(results: list[ModelResult]) -> None:
    ranked = rank_results(results)

    print("\n=== MATRIX (best first) ===")
    print(f"{'model':<42} {'prov':<11} {'pri':>3} {'pass':>5} {'lat(s)':>7}")
    for r in ranked:
        lat = f"{r.total_latency:.1f}" if r.passed else "-"
        print(
            f"{r.model_name:<42} {r.provider:<11} {r.priority:>3} "
            f"{r.passed}/{len(r.probes):<2} {lat:>7}"
        )

    failures = [
        (r, p) for r in ranked for p in r.probes if p.outcome != "ok"
    ]
    if failures:
        print("\n=== FAILURES ===")
        for r, p in failures:
            print(f"{r.model_name} [{p.label}] {p.outcome}: {p.detail}")


def dump_json(results: list[ModelResult], out: Path) -> None:
    payload = [
        {
            "model": r.model_name,
            "provider": r.provider,
            "priority": r.priority,
            "capabilities": list(r.capabilities),
            "passed": r.passed,
            "total_probes": len(r.probes),
            "latency_s": round(r.total_latency, 2),
            "probes": [
                {
                    "label": p.label,
                    "outcome": p.outcome,
                    "latency_s": round(p.latency_s, 2),
                    "detail": p.detail,
                }
                for p in r.probes
            ],
        }
        for r in rank_results(results)
    ]
    out.write_text(json.dumps(payload, indent=2))
    print(f"\nraw json -> {out}")


def main() -> None:  # pragma: no cover - CLI wrapper
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="probe models whose name contains SUBSTR")
    parser.add_argument(
        "--embeddings", action="store_true", help="also probe embedding models"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(tempfile.gettempdir()) / "model-sweep.json",
        help="where to write raw JSON results",
    )
    args = parser.parse_args()

    results: list[ModelResult] = []

    if not args.embeddings or args.only:
        from draftly.models.factory import build_model_router

        results.extend(sweep(build_model_router(), args.only))

    if args.embeddings:
        print("probing embedding models...")
        results.extend(probe_embeddings())

    if not results:
        print("nothing to probe")
        return

    print_report(results)
    dump_json(results, args.out)


if __name__ == "__main__":
    main()
