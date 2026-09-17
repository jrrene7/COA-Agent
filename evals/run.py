"""Run the EvaluatorAgent against the labelled corpus.

This makes real API calls, so it is NOT part of the pytest suite — the project
constitution requires unit tests to run without credentials. Run it deliberately:

    python -m evals.run
    python -m evals.run --model gpt-4o --json

Exits non-zero when the evaluator would have approved a draft a human labelled
'review', which is the failure that actually costs something.
"""

import argparse
import json
import os
import sys

from lib.logging_config import configure_logging

from evals.cases import CASES
from evals.harness import (
    DEFAULT_MAX_FALSE_APPROVALS,
    DEFAULT_MIN_AGREEMENT,
    format_report,
    score_case,
    summarize,
)


def run_evals(model: str, verbose: bool = True) -> "object":
    from agents.evaluator_agent import EvaluatorAgent, EvaluatorAgentError

    agent = EvaluatorAgent(model=model)
    results = []

    for case in CASES:
        if verbose:
            print(f"  evaluating {case.case_id} …", flush=True)
        try:
            evaluation = agent.run(case.research, case.marketing, case.sales)
            results.append(score_case(case, evaluation))
        except EvaluatorAgentError as exc:
            results.append(score_case(case, {}, error=str(exc)))

    return summarize(results)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the EvaluatorAgent")
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    parser.add_argument(
        "--max-false-approvals",
        type=int,
        default=DEFAULT_MAX_FALSE_APPROVALS,
        help="fail above this many bad drafts approved (default 0)",
    )
    parser.add_argument("--min-agreement", type=float, default=DEFAULT_MIN_AGREEMENT)
    args = parser.parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        print(
            "OPENAI_API_KEY is not set. These evals call the real model by design —\n"
            "set it in config.env or the environment and re-run.",
            file=sys.stderr,
        )
        sys.exit(2)

    configure_logging()
    summary = run_evals(args.model, verbose=not args.json)
    passed = summary.passed(args.max_false_approvals, args.min_agreement)

    if args.json:
        print(json.dumps({
            "model": args.model,
            "total": summary.total,
            "scored": summary.scored,
            "errors": summary.errors,
            "agreement": summary.agreement,
            "false_approvals": summary.false_approvals,
            "false_reviews": summary.false_reviews,
            "score_ranges": summary.score_ranges,
            "passed": passed,
            "cases": [
                {
                    "case_id": r.case_id,
                    "expected": r.expected,
                    "actual": r.actual,
                    "overall": r.overall,
                    "verdict": r.verdict,
                    "error": r.error,
                }
                for r in summary.results
            ],
        }, indent=2))
    else:
        print(format_report(summary))
        print("PASS" if passed else "FAIL")

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
