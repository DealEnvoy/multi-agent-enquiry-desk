"""Command line for the enquiry desk."""

from __future__ import annotations

import argparse
import os
import sys

from enquiry_desk.errors import ConfigError
from enquiry_desk.graph import build_graph, invoke_until_pause, resume
from enquiry_desk.io import load_enquiry
from enquiry_desk.models import StubModel, build_model
from enquiry_desk.present import render_close, render_pause
from enquiry_desk.state import initial_state
from enquiry_desk.workflow import DEFAULT_MAX_REVIEW_ROUNDS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="enquiry-desk",
        description="Multi-agent enquiry desk demo for a fictional lettings agency.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run one enquiry through the desk.")
    run.add_argument("enquiry", help="Path to an enquiry JSON file.")
    run.add_argument("--decision", choices=("approve", "reject"), help="Skip the interactive prompt.")
    run.add_argument("--note", default="", help="Optional note stored with the decision.")
    run.add_argument("--log-dir", default="runs", help="Directory for the JSON run log.")
    run.add_argument("--model", choices=("stub", "anthropic", "openai"), help="Override ENQUIRY_DESK_MODEL.")
    run.add_argument("--max-review-rounds", type=int, default=DEFAULT_MAX_REVIEW_ROUNDS)
    run.set_defaults(func=cmd_run)

    demo = sub.add_parser("demo", help="Run the fee-waiver example on the offline stub.")
    demo.add_argument("--log-dir", default="runs")
    demo.set_defaults(func=cmd_demo)

    diagram = sub.add_parser("diagram", help="Print the compiled graph as mermaid.")
    diagram.set_defaults(func=cmd_diagram)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


def cmd_demo(args: argparse.Namespace) -> int:
    args.enquiry = "examples/fee-waiver.json"
    args.decision = "approve"
    args.note = ""
    args.model = "stub"
    args.max_review_rounds = DEFAULT_MAX_REVIEW_ROUNDS
    return cmd_run(args)


def cmd_diagram(_args: argparse.Namespace) -> int:
    graph = build_graph(StubModel(), log_dir=None)
    print(graph.get_graph().draw_mermaid())
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    if args.max_review_rounds < 1:
        raise ValueError("--max-review-rounds must be at least 1.")
    model = _resolve_model(args.model)
    enquiry = load_enquiry(args.enquiry)
    graph = build_graph(
        model,
        log_dir=args.log_dir,
        max_review_rounds=args.max_review_rounds,
    )
    state = initial_state(enquiry, model_name=model.name)
    _config, pause = invoke_until_pause(graph, state, thread_id=enquiry["enquiry_id"])
    if pause.next != ("approval",):
        print("The desk finished without an approval checkpoint.", file=sys.stderr)
        return 1
    print(render_pause(pause.values, model_label=_model_label(model)), end="")
    decision = args.decision or _ask()
    final = resume(graph, _config, {"action": decision, "note": args.note})
    print(render_close(final.values), end="")
    return 0


def _resolve_model(choice: str | None):
    if choice is None:
        return build_model()
    if choice == "stub":
        return StubModel()
    return build_model({**os.environ, "ENQUIRY_DESK_MODEL": choice})


def _model_label(model) -> str:
    if getattr(model, "name", "") == "stub":
        return "stub (offline, no API key)"
    provider = getattr(model, "provider", "live")
    return f"{provider} {model.name}"


def _ask() -> str:
    if not sys.stdin.isatty():
        raise ValueError("Pass --decision approve or --decision reject. There is no terminal to ask.")
    while True:
        typed = input("Decision [approve/reject]: ").strip().lower()
        if typed in {"approve", "reject"}:
            return typed
        print("Type approve or reject.")
