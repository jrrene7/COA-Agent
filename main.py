import argparse
import json
import sys

from agents.inbound_orchestrator import InboundOrchestrator, InboundOrchestratorError
from agents.orchestrator import Orchestrator, OrchestratorError
from lib import kpi
from lib.logging_config import configure_logging
from lib.persistence import RunStore


def _print_kpis(store: RunStore, limit: int) -> None:
    runs = store.recent_runs(limit=limit)
    print(json.dumps(kpi.summarize(runs), indent=2))
    print(f"\nLast {min(limit, len(runs))} runs:")
    for row in runs:
        print(
            f"  {row['started_at'][:19]}  {row['direction']:<8} {row['status']:<10} "
            f"{(row['queue'] or '-'):<13} {row['company'][:24]:<24} "
            f"tokens={row['total_tokens'] or 0:<7} cost=${row['estimated_cost_usd'] or 0:.4f}"
        )


def _run_outbound(args, store: RunStore) -> None:
    orchestrator = Orchestrator(model=args.model, verbose=True, store=store)
    try:
        run = orchestrator.run(company=args.company, url=args.url)
    except OrchestratorError as exc:
        print(f"\n✗ Pipeline failed: {exc}", file=sys.stderr)
        sys.exit(1)

    final = run.get_final_state()
    stored = store.get_run(run.run_id) or {}
    routing = final.get("routing", {})
    evaluation = final.get("evaluation", {})

    print(f"\n✓ Report written to: {final['report_path']}")
    print(f"  ICP fit score:  {final['marketing_data'].get('icp_fit', 'N/A')}")
    print(f"  Email subject:  {final['sales_data'].get('email', {}).get('subject', '')}")
    print(f"  Draft quality:  {evaluation.get('overall', 'N/A')}/10 "
          f"({evaluation.get('verdict', 'n/a')}, reads {evaluation.get('sentiment', 'n/a')})")
    print(f"  Routed to:      {routing.get('queue', 'n/a')} / {routing.get('owner', 'n/a')}"
          f"{'  ** NEEDS HUMAN REVIEW **' if routing.get('escalate') else ''}")
    print(f"  Run id:         {run.run_id}")
    print(f"  Tokens:         {stored.get('total_tokens', 0)} "
          f"(~${stored.get('estimated_cost_usd', 0):.4f})")


def _run_inbound(args, store: RunStore) -> None:
    body = args.body
    if body == "-":
        body = sys.stdin.read()

    orchestrator = InboundOrchestrator(model=args.model, verbose=True, store=store)
    try:
        run = orchestrator.run(
            {
                "sender": args.sender,
                "subject": args.subject,
                "body": body,
                "channel": args.channel,
            }
        )
    except InboundOrchestratorError as exc:
        print(f"\n✗ Inbound pipeline failed: {exc}", file=sys.stderr)
        sys.exit(1)

    final = run.get_final_state()
    triage = final.get("triage", {})
    routing = final.get("routing", {})

    print(f"\n✓ Note written to: {final['report_path']}")
    print(f"  Sentiment:      {triage.get('sentiment', 'n/a')}")
    print(f"  Intent:         {triage.get('intent', 'n/a')}")
    print(f"  Urgency:        {triage.get('urgency', 'n/a')}")
    print(f"  Routed to:      {routing.get('queue', 'n/a')} / {routing.get('owner', 'n/a')} "
          f"({routing.get('priority', 'n/a')})")
    if routing.get("escalate"):
        print(f"  ** ESCALATED **  {routing.get('reason', '')}")
        print("  No reply was drafted — a human owns this message.")
    else:
        print(f"  Reply drafted:  {final.get('reply', {}).get('subject', '')} "
              f"(confidence {final.get('reply', {}).get('confidence', 'n/a')}/10, DRAFT)")
    print(f"  Run id:         {run.run_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Customer Outreach Automation Agent")
    parser.add_argument(
        "--mode",
        choices=("outbound", "inbound"),
        default="outbound",
        help="outbound researches a lead; inbound triages a customer message",
    )
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--kpis", action="store_true", help="print KPI summary and exit")
    parser.add_argument("--limit", type=int, default=20, help="runs to include in --kpis")

    outbound = parser.add_argument_group("outbound")
    outbound.add_argument("--company", default="Stripe", help="lead company name")
    outbound.add_argument("--url", default="https://stripe.com", help="lead website URL")

    inbound = parser.add_argument_group("inbound")
    inbound.add_argument("--sender", default="", help="who sent the message")
    inbound.add_argument("--subject", default="", help="message subject")
    inbound.add_argument("--body", default="", help="message body, or '-' to read stdin")
    inbound.add_argument("--channel", default="email", help="email, chat, form …")

    args = parser.parse_args()

    configure_logging()
    store = RunStore()

    if args.kpis:
        _print_kpis(store, args.limit)
        return

    if args.mode == "inbound":
        if not args.body:
            parser.error("--body (or '-' for stdin) is required in inbound mode")
        _run_inbound(args, store)
    else:
        _run_outbound(args, store)


if __name__ == "__main__":
    main()
