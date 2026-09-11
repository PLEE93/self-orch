"""CLI: stdout = terminal substrate_group JSON. Live panels go to stderr."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .display import Dashboard
from .doctrine import GOVERNOR_LOOP
from .install import doctor as install_doctor
from .install import main as install_main
from .rail import SeatSpec, dispatch
from .stages import (
    STAGE_ORDER, STAGE_TIERS, PipelineRefusal, run_pipeline,
)


def _load_spec(path: Path) -> dict:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise SystemExit("spec must be a JSON object")
    return data


def _seats_from_args(args: argparse.Namespace, spec: dict | None) -> tuple[list[dict], str, str]:
    if spec:
        seats = spec.get("seats") or spec.get("substrate_specs") or []
        user_msg = spec.get("user_msg") or spec.get("user") or args.user_msg or ""
        level = spec.get("level") or args.level
        return seats, user_msg, level
    seats = []
    for raw in args.seat or []:
        seats.append(_parse_seat_flag(raw))
    return seats, args.user_msg or "", args.level


def _parse_seat_flag(raw: str) -> dict:
    """role=...,model=...,brief=...  (brief last so it may contain commas)"""
    parts = {}
    for chunk in raw.split(","):
        if "=" not in chunk:
            continue
        k, _, v = chunk.partition("=")
        k = k.strip()
        if k in parts and k == "brief":
            parts[k] = parts[k] + "," + v
        else:
            parts[k.strip()] = v
    # Re-join brief if we split on commas inside it: use dedicated marker
    if "brief" not in parts:
        # allow role=x;model=y;brief=z
        parts = {}
        for chunk in raw.split(";"):
            if "=" not in chunk:
                continue
            k, _, v = chunk.partition("=")
            parts[k.strip()] = v
    return {
        "role": parts.get("role", ""),
        "model": parts.get("model", ""),
        "brief": parts.get("brief", ""),
        "phase": parts.get("phase", ""),
        "query_angle": parts.get("angle") or parts.get("query_angle") or "",
    }


def cmd_dispatch(args: argparse.Namespace) -> int:
    spec = _load_spec(Path(args.spec)) if args.spec else None
    seats, user_msg, level = _seats_from_args(args, spec)
    if not seats:
        print("need --spec or --seat", file=sys.stderr)
        return 2
    if not user_msg:
        print("need --user-msg or spec.user_msg", file=sys.stderr)
        return 2
    specs = [SeatSpec.from_dict(s) for s in seats]
    dash_state = [
        {"role": s.role, "model": s.model, "ui_status": "pending", "preview": "", "elapsed_s": None}
        for s in specs
    ]
    dash = Dashboard(dash_state, enabled=not args.quiet)
    dash.refresh()

    def on_update(live: list[dict]) -> None:
        for i, row in enumerate(live):
            dash_state[i].update(row)
        dash.refresh()

    result = dispatch(specs, user_msg=user_msg, level=level, on_update=on_update)
    dash.finish()
    payload = result.to_dict()
    if args.out:
        Path(args.out).write_text(json.dumps(payload, indent=2) + "\n")
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if result.dispatch_state == "completed" else 1


def cmd_stages(_args: argparse.Namespace) -> int:
    """Print the enforced stage order and the tier each stage runs on."""
    from .tiers import resolve_tier
    rows = []
    for name in STAGE_ORDER:
        tier = STAGE_TIERS[name]
        rows.append({"stage": name, "tier": tier, "model": resolve_tier(tier)})
    json.dump({"stage_order": list(STAGE_ORDER), "stages": rows}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_pipeline(args: argparse.Namespace) -> int:
    """Run the full seven-stage process, not a single same-turn fanout."""
    user_msg = args.user_msg or ""
    if not user_msg:
        print("need --user-msg", file=sys.stderr)
        return 2

    def on_stage(run) -> None:
        if args.quiet:
            return
        extra = (" verdict=" + run.verdict) if run.verdict else ""
        print(
            "[stage] %-9s tier=%-5s model=%-22s seats=%d loop=%d %.1fs%s"
            % (run.stage, run.tier, run.model, run.seats, run.loop, run.elapsed_s, extra),
            file=sys.stderr,
        )

    try:
        result = run_pipeline(
            user_msg,
            gather_seats=args.gather,
            execute_seats=args.execute,
            max_loops=args.max_loops,
            level=args.level,
            on_stage=on_stage,
        )
    except PipelineRefusal as e:
        json.dump({"state": "refused", "reason": str(e)}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        print("pipeline refused: %s" % e, file=sys.stderr)
        return 2

    payload = result.to_dict()
    if args.out:
        Path(args.out).write_text(json.dumps(payload, indent=2) + "\n")
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if result.state == "completed" else 1


def cmd_doctrine(_args: argparse.Namespace) -> int:
    sys.stdout.write(GOVERNOR_LOOP)
    if not GOVERNOR_LOOP.endswith("\n"):
        sys.stdout.write("\n")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    argv = ["--agent", args.agent, "--scope", args.scope]
    if args.skip_cli:
        argv.append("--skip-cli")
    if args.dry_run:
        argv.append("--dry-run")
    if args.prefix:
        argv.extend(["--prefix", args.prefix])
    if args.project:
        argv.extend(["--project", args.project])
    return install_main(argv)


def cmd_doctor(args: argparse.Namespace) -> int:
    payload = install_doctor(None if args.agent == "auto" else args.agent)
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    keys = payload.get("keys") or {}
    creds = payload.get("credentials") or {}
    # A user who authenticated their Claude Code / Codex / Grok CLI holds NO
    # API key and is perfectly runnable -- that account-token path is the
    # headline setup this rail supports. Judging on keys alone reported
    # failure for exactly that user.
    have_key = any(keys.values())
    have_cred = any(v and v != "none" for v in creds.values())
    if not have_key and not have_cred:
        print(
            "No usable model credential. Either set an API key (ZAI_API_KEY, "
            "XAI_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY) or log in to a "
            "coding agent CLI (Claude Code, Codex, Grok) and self-orch will "
            "reuse that session.",
            file=sys.stderr,
        )
        return 1
    if not payload.get("cli"):
        print("self-orch not on PATH. Run: python3 scripts/install.py --agent auto", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="self-orch",
        description="Parallel substrate rail. The coding agent is the governor.",
    )
    p.add_argument("--version", action="version", version=f"self-orch {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("dispatch", help="run N seats in parallel, print substrate_group JSON")
    d.add_argument("--spec", help="JSON spec file")
    d.add_argument("--user-msg", default="")
    d.add_argument("--level", default="standard", choices=["low", "med", "standard", "high"])
    d.add_argument(
        "--seat",
        action="append",
        help='seat as role=...;model=...;brief=...  (repeatable, use ; separators)',
    )
    d.add_argument("--out", help="also write JSON to this path")
    d.add_argument("--quiet", action="store_true", help="no stderr dashboard")
    d.set_defaults(func=cmd_dispatch)

    pl = sub.add_parser(
        "pipeline",
        help="run the full seven-stage process (orient->gather->organize->heavy"
             "->review->execute->redteam->deliver, looping on red-team FAIL)",
    )
    pl.add_argument("--user-msg", default="", help="the task, in the user's own words")
    pl.add_argument("--gather", type=int, default=3, help="parallel context-gathering seats")
    pl.add_argument("--execute", type=int, default=2, help="parallel execution seats")
    pl.add_argument("--max-loops", type=int, default=2,
                    help="how many times execute may be re-run after a red-team FAIL")
    pl.add_argument("--level", default="standard", choices=["low", "med", "standard", "high"])
    pl.add_argument("--out", help="also write the result JSON to this path")
    pl.add_argument("--quiet", action="store_true", help="no stderr stage log")
    pl.set_defaults(func=cmd_pipeline)

    st = sub.add_parser("stages", help="print the enforced stage order and each stage's tier")
    st.set_defaults(func=cmd_stages)

    g = sub.add_parser("doctrine", help="print governor loop for injection into any agent")
    g.set_defaults(func=cmd_doctrine)

    i = sub.add_parser("install", help="install CLI + wire skill into this coding agent")
    i.add_argument("--agent", default="auto")
    i.add_argument("--scope", default="user", choices=["user", "project"])
    i.add_argument("--prefix", default="")
    i.add_argument("--project", default=".")
    i.add_argument("--skip-cli", action="store_true")
    i.add_argument("--dry-run", action="store_true")
    i.set_defaults(func=cmd_install)

    doc = sub.add_parser("doctor", help="check CLI, keys, and skill wiring")
    doc.add_argument("--agent", default="auto")
    doc.set_defaults(func=cmd_doctor)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
