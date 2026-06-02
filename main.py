import logging
import sys

from agents.orchestrator import Orchestrator, OrchestratorError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def main() -> None:
    orchestrator = Orchestrator(model="gpt-4o-mini", verbose=True)

    try:
        run = orchestrator.run(
            company="Stripe",
            url="https://stripe.com",
        )
    except OrchestratorError as exc:
        print(f"\n✗ Pipeline failed: {exc}", file=sys.stderr)
        sys.exit(1)

    final = run.get_final_state()
    print(f"\n✓ Report written to: {final['report_path']}")
    print(f"  ICP fit score:  {final['marketing_data'].get('icp_fit', 'N/A')}")
    print(f"  Email subject:  {final['sales_data'].get('email', {}).get('subject', '')}")


if __name__ == "__main__":
    main()
