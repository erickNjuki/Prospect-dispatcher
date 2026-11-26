"""CLI.

  python3 -m router samples/leads/01_demo_request.json        JSON plan, nothing saved
  python3 -m router samples/leads/*.json --text               readable decision path per lead
  python3 -m router lead.json --commit                         also save the round-robin pointer
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .route import Context, route

ROOT = Path(__file__).resolve().parent.parent


class ConfigError(ValueError):
    pass


def check_config(cfg: dict) -> None:
    """Fail at load time on settings that would otherwise crash or misroute mid-batch."""
    problems = []
    for key in ("name_match_threshold", "personal_domains", "do_not_contact", "territories",
                "fallback_pool", "sources", "nurture_sequence"):
        if key not in cfg:
            problems.append(f"missing '{key}'")
    if problems:
        raise ConfigError("routing.json: " + "; ".join(problems))
    if not 0 < cfg["name_match_threshold"] <= 1:
        problems.append("name_match_threshold must be in (0, 1]")
    if "contact_sales" not in cfg["sources"]:
        problems.append("sources must include 'contact_sales' (the default for unknown sources)")
    for name, src in cfg["sources"].items():
        if not isinstance(src.get("sla_minutes"), int) or src["sla_minutes"] <= 0 or not src.get("sequence"):
            problems.append(f"source '{name}' needs a positive integer sla_minutes and a sequence")
    if not cfg["fallback_pool"]:
        problems.append("fallback_pool is empty")
    names = [t.get("name") for t in cfg["territories"]]
    if len(set(names)) != len(names):
        problems.append("territory names must be unique (they key the round-robin state)")
    for t in cfg["territories"]:
        if not t.get("pool"):
            problems.append(f"territory '{t.get('name')}' has an empty pool")
    for key in ("emails", "domains"):
        if key not in cfg["do_not_contact"]:
            problems.append(f"do_not_contact.{key} is missing")
    if problems:
        raise ConfigError("routing.json: " + "; ".join(problems))


def load_context(data: Path, state_file: Path) -> Context:
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    config = json.loads((data / "routing.json").read_text())
    check_config(config)
    return Context(accounts=json.loads((data / "accounts.json").read_text()),
                   enrichment={k.lower(): v for k, v in json.loads((data / "enrichment.json").read_text()).items()},
                   config=config,
                   rr_state=state)


def show(name: str, plan) -> str:
    out = [f"== {name}", *[f"   {p}" for p in plan.path], f"   -> {plan.outcome}, owner {plan.owner}"]
    for a in plan.actions:
        detail = ", ".join(f"{k}={v}" for k, v in a.items() if k != "type")
        out.append(f"   would {a['type']}: {detail}")
    return "\n".join(out)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="router", description="Plan how to route inbound leads. Writes nothing by default.")
    p.add_argument("leads", nargs="+", type=Path, help="lead JSON files")
    p.add_argument("--data", type=Path, default=ROOT / "data")
    p.add_argument("--state", type=Path, default=ROOT / "state" / "round_robin.json")
    p.add_argument("--text", action="store_true", help="readable decision path instead of JSON")
    p.add_argument("--commit", action="store_true", help="save the round-robin pointer after planning")
    args = p.parse_args(argv)

    try:
        ctx = load_context(args.data, args.state)
    except (ConfigError, OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    plans, failed = [], 0
    for f in args.leads:
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("expected one JSON object per file")
        except (OSError, ValueError) as e:  # JSONDecodeError is a ValueError
            print(f"error: {f}: {e}", file=sys.stderr)
            failed += 1
            continue
        plan = route(raw, ctx)
        plans.append(plan)
        if args.text:
            print(show(f.name, plan) + "\n")
    if not args.text and plans:
        body = [pl.as_dict() for pl in plans]
        print(json.dumps(body[0] if len(body) == 1 else body, indent=2))
    if args.commit:
        args.state.parent.mkdir(parents=True, exist_ok=True)
        args.state.write_text(json.dumps(ctx.rr_state, indent=2, sort_keys=True))
        print(f"saved round-robin state to {args.state}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
