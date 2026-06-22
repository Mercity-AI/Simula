from __future__ import annotations

import argparse
import asyncio
import time

from .config import load_config
from .console import error, info
from .evaluate import run_evaluation
from .generate import generate_dataset
from .models import ModelRouter
from .taxonomy import build_taxonomy
from .utils import artifact_path, summarize_cost, write_json


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main(argv))


async def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="syndata", description="Schema-driven synthetic data generator.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "taxonomy", "generate", "evaluate", "run"):
        cmd = sub.add_parser(name)
        cmd.add_argument("config")
        cmd.add_argument("--quiet", action="store_true", help="Suppress progress bars.")
        if name in {"generate", "run"}:
            resume = cmd.add_mutually_exclusive_group()
            resume.add_argument("--resume", dest="resume", action="store_true", default=True)
            resume.add_argument("--no-resume", dest="resume", action="store_false")

    args = parser.parse_args(argv)
    cfg = None
    router = None
    try:
        cfg = load_config(args.config)
        router = ModelRouter(cfg.data)

        # Validate prints static run information and does not invoke any model calls.
        if args.command == "validate":
            info(f"Config OK: {cfg.path}")
            info(f"Output dir: {cfg.output_dir}")
            info(f"Output format: {cfg.output_format}")
            return 0

        if args.command == "taxonomy":
            taxonomy = await build_taxonomy(cfg, router)
            info(f"Wrote taxonomy with {len(taxonomy.get('factors', []))} factors to {cfg.output_dir}")
            return 0

        if args.command == "generate":
            rows = await generate_dataset(cfg, router, resume=args.resume, quiet=args.quiet)
            info(f"Wrote {len(rows)} final records to {cfg.output_dir}")
            return 0

        if args.command == "evaluate":
            report = await run_evaluation(cfg, router, quiet=args.quiet)
            info(f"Wrote eval report for {report.get('count', 0)} records to {cfg.output_dir}")
            return 0

        if args.command == "run":
            # generate_dataset loads-or-builds the taxonomy itself; building it here too would
            # rebuild and overwrite an edited/earlier taxonomy on every resumed `run`.
            rows = await generate_dataset(cfg, router, resume=args.resume, quiet=args.quiet)
            report = await run_evaluation(cfg, router, quiet=args.quiet)
            info(f"Run complete: {len(rows)} final records, eval count={report.get('count', 0)}")
            info(f"Artifacts: {cfg.output_dir}")
            return 0
    except Exception as exc:
        error(str(exc))
        return 1
    finally:
        # Flush live LLM logs and persist cost totals before the process exits.
        if router is not None:
            await router.flush_logs()
        if cfg is not None and router is not None:
            summary = summarize_cost(router.cost, time.time() - router.started)
            write_json(artifact_path(cfg.output_dir, "cost"), summary)
            if summary["total_calls"]:
                info(
                    f"Cost summary: calls={summary['total_calls']} "
                    f"in_tokens={summary['total_input_tokens']} out_tokens={summary['total_output_tokens']}"
                )

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
