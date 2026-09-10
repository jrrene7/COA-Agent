import json
import logging
import sys
from pathlib import Path

from agents.orchestrator import Orchestrator, OrchestratorError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

LEADS = [
    {"company": "Stripe", "url": "https://stripe.com"},
    {"company": "Linear", "url": "https://linear.app"},
    {"company": "Vercel", "url": "https://vercel.com"},
    {"company": "Retool", "url": "https://retool.com"},
]


def run_batch(leads: list) -> list:
    orchestrator = Orchestrator(model="gpt-4o-mini", verbose=True)
    results = []

    for lead in leads:
        company = lead.get("company", "unknown")
        try:
            run = orchestrator.run(company, lead.get("url", ""))
            state = run.get_final_state()
            results.append(
                {
                    "company": company,
                    "report_path": state["report_path"],
                    "icp_fit": state["marketing_data"].get("icp_fit"),
                    "status": "ok",
                }
            )
        except OrchestratorError as exc:
            results.append({"company": company, "status": f"error: {exc}"})

    summary_path = Path("output/batch_summary.json")
    summary_path.parent.mkdir(exist_ok=True)
    summary_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nBatch complete. Summary → {summary_path}")
    return results


def main() -> None:
    results = run_batch(LEADS)
    for r in results:
        ok = r["status"] == "ok"
        icon = "✓" if ok else "✗"
        report = r.get("report_path", "")
        fit = r.get("icp_fit", "—")
        print(f"  {icon} {r['company']}: ICP {fit} → {report}")

    failures = [r for r in results if r["status"] != "ok"]
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
