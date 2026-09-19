"""Live sweep for the Nebius Token Factory Nemotron-3 models + Qwen embedder.

Usage:
    uv run python tests/scripts/probe_token_factory.py [--only SUBSTR]
        [--embeddings] [--out PATH]

Mirrors ``probe_models.py`` but scoped to the ``nebius_token_factory``
provider so a full-suite sweep isn't needed. Probes each registered
Nemotron model with capability-matched checks (basic completion, tool
invocation asserted via real toolUse blocks, structured output, a
tool-less streaming call that records TTFT + token cost, and a
context-window smoke test at the model's advertised window), plus the
TF embedding endpoint at 1536 dims. Prints a matrix ranked by
probes-passed then total latency and writes raw JSON results.

Requires live Token Factory keys in .env. Nothing is written to the repo.
"""

from __future__ import annotations

import argparse
import asyncio
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

PROBE_TIMEOUT_S = 300.0
TOKEN_FACTORY_PROVIDER = "nebius_token_factory"


@dataclass
class ProbeResult:
    label: str
    outcome: str  # "ok" | error class name | "TIMEOUT"
    latency_s: float = 0.0
    detail: str = ""
    ttft_s: float | None = None
    cost_usd: float | None = None


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

    @property
    def ttft_s(self) -> float | None:
        values = [p.ttft_s for p in self.probes if p.ttft_s is not None]
        return values[0] if values else None

    @property
    def cost_usd(self) -> float | None:
        values = [p.cost_usd for p in self.probes if p.cost_usd is not None]
        return sum(values) if values else None


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


def probe_ttft_cost(router, cfg) -> ProbeResult:
    """Tool-less streaming call: first-token latency + estimated cost.

    Uses a fresh agent with no tools so this also covers the tool-less
    request path (which Token Factory rejects if ``tools: []`` is sent).
    """
    from strands import Agent

    provider = router.registry.get_provider(cfg.provider)
    agent = Agent(model=provider.create_model(cfg), callback_handler=None)

    start = time.monotonic()
    first_token_at: float | None = None
    result = None

    async def _run() -> None:
        nonlocal first_token_at, result
        async for event in agent.stream_async("Reply with exactly: pong"):
            if first_token_at is None and event.get("data"):
                first_token_at = time.monotonic() - start
            if "result" in event:
                result = event["result"]

    try:
        asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 - the sweep reports any failure
        return ProbeResult(
            "ttft_cost",
            type(exc).__name__,
            time.monotonic() - start,
            str(exc)[:160],
        )

    latency = time.monotonic() - start
    usage = getattr(getattr(result, "metrics", None), "accumulated_usage", None) or {}
    in_tok = int(usage.get("inputTokens") or 0)
    out_tok = int(usage.get("outputTokens") or 0)

    cost: float | None = None
    if (
        cfg.input_cost_per_1m_tokens is not None
        and cfg.output_cost_per_1m_tokens is not None
    ):
        cost = (
            in_tok * cfg.input_cost_per_1m_tokens
            + out_tok * cfg.output_cost_per_1m_tokens
        ) / 1_000_000

    parts = [
        f"ttft={first_token_at:.2f}s" if first_token_at is not None else "ttft=n/a",
        f"in={in_tok}",
        f"out={out_tok}",
    ]
    if cost is not None:
        parts.append(f"cost=${cost:.6f}")

    return ProbeResult(
        "ttft_cost", "ok", latency, " ".join(parts),
        ttft_s=first_token_at, cost_usd=cost,
    )


def probe_model(router, cfg, quick: bool = False) -> ModelResult:
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

    result.probes.append(probe_ttft_cost(router, cfg))

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
                lambda: agent(
                    "What city has roughly 8.5 million people? Fill the schema.",
                    structured_output_model=ProbeAnswer,
                ),
                "structured",
            )
        )

    if cfg.context_window and not quick:
        window = cfg.context_window
        # ~4 chars/token on average: build a filler document at ~90% of the
        # advertised window (tokenizer variance margin) so the completion
        # must succeed at near-window size.
        filler = ("Nemotron context smoke test. " * (window // 7))[
            : int(window * 4 * 0.9)
        ]

        def context_probe():
            # Use the Agent created above (same model config) so the long
            # context request goes through the same path as the other probes.
            echo = agent(
                f"{filler}\n\nRead the filler above, then type exactly: ok"
            )
            if "ok" not in str(echo).lower():
                raise AssertionError(
                    f"no 'ok' completion at ~{window} token window"
                )
            return "ok"

        result.probes.append(run_probe(context_probe, "context_window"))

    return result


def probe_embeddings() -> list[ModelResult]:
    from draftly.models.factory import build_embedding_router

    router = build_embedding_router()
    results: list[ModelResult] = []

    for cfg in router.registry.list_embedding_models():
        if cfg.provider != TOKEN_FACTORY_PROVIDER:
            continue
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

        def dims_probe():
            vec = embedder.embed_query("The quick brown fox jumps over the lazy dog.")
            if len(vec) != cfg.dimensions:
                raise AssertionError(
                    f"expected {cfg.dimensions} dims, got {len(vec)}"
                )
            return f"dims={len(vec)}"

        result.probes.append(run_probe(dims_probe, "embed_1536"))
        results.append(result)

    return results


def sweep(router, only: str | None, quick: bool = False) -> list[ModelResult]:
    models = [
        m for m in router.registry.list_models()
        if m.provider == TOKEN_FACTORY_PROVIDER
    ]
    if only:
        models = [m for m in models if only.lower() in m.name]

    print(f"probing {len(models)} Token Factory chat models...")

    results: list[ModelResult] = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(probe_model, router, cfg, quick): cfg.name
            for cfg in models
        }
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                res = fut.result(timeout=PROBE_TIMEOUT_S * 3)
            except TimeoutError:
                res = ModelResult(name, TOKEN_FACTORY_PROVIDER, 0, ())
                res.probes.append(ProbeResult("sweep", "TIMEOUT"))
            except Exception as exc:  # noqa: BLE001
                res = ModelResult(name, TOKEN_FACTORY_PROVIDER, 0, ())
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
    print(
        f"{'model':<42} {'prov':<11} {'pri':>3} {'pass':>5} "
        f"{'lat(s)':>7} {'ttft(s)':>7} {'cost$':>9}"
    )
    for r in ranked:
        lat = f"{r.total_latency:.1f}" if r.passed else "-"
        ttft = f"{r.ttft_s:.2f}" if r.ttft_s is not None else "-"
        cost = f"{r.cost_usd:.6f}" if r.cost_usd is not None else "-"
        print(
            f"{r.model_name:<42} {r.provider:<11} {r.priority:>3} "
            f"{r.passed}/{len(r.probes):<2} {lat:>7} {ttft:>7} {cost:>9}"
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
            "ttft_s": round(r.ttft_s, 3) if r.ttft_s is not None else None,
            "cost_usd": round(r.cost_usd, 6) if r.cost_usd is not None else None,
            "probes": [
                {
                    "label": p.label,
                    "outcome": p.outcome,
                    "latency_s": round(p.latency_s, 2),
                    "ttft_s": round(p.ttft_s, 3) if p.ttft_s is not None else None,
                    "cost_usd": (
                        round(p.cost_usd, 6) if p.cost_usd is not None else None
                    ),
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
        "--quick",
        action="store_true",
        help="skip context-window smoke probes (faster, cheaper)",
    )
    parser.add_argument(
        "--embeddings", action="store_true", help="also probe the TF embedder"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(tempfile.gettempdir()) / "token-factory-sweep.json",
        help="where to write raw JSON results",
    )
    args = parser.parse_args()

    from draftly.models.factory import build_model_router

    results: list[ModelResult] = []
    results.extend(sweep(build_model_router(), args.only, args.quick))

    if args.embeddings:
        print("probing Token Factory embedding models...")
        results.extend(probe_embeddings())

    if not results:
        print("nothing to probe")
        return

    print_report(results)
    dump_json(results, args.out)


if __name__ == "__main__":
    main()
