"""CLI: stdout = terminal substrate_group JSON. Live panels go to stderr."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .display import Dashboard
from .doctrine import GOVERNOR_LOOP
from .rail import SeatSpec, dispatch


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


def cmd_doctrine(_args: argparse.Namespace) -> int:
    sys.stdout.write(GOVERNOR_LOOP)
    if not GOVERNOR_LOOP.endswith("\n"):
        sys.stdout.write("\n")
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

    g = sub.add_parser("doctrine", help="print governor loop for injection into any agent")
    g.set_defaults(func=cmd_doctrine)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
