import os
import unittest
from unittest.mock import patch

from tests.external_stubs import install

install()

from lib import kpi


class EstimateCostTests(unittest.TestCase):
    def test_known_model_prices_prompt_and_completion_separately(self):
        # 1M prompt @ $0.15 + 1M completion @ $0.60
        self.assertAlmostEqual(
            kpi.estimate_cost_usd("gpt-4o-mini", 1_000_000, 1_000_000), 0.75
        )

    def test_unknown_model_costs_zero_rather_than_guessing(self):
        self.assertEqual(kpi.estimate_cost_usd("some-future-model", 10_000, 10_000), 0.0)


class TrackRunTests(unittest.TestCase):
    def test_records_are_noops_outside_an_active_run(self):
        """Agents must stay independently testable without a run context."""
        self.assertIsNone(kpi.current())
        kpi.record_llm_usage("gpt-4o-mini", 100, 50, 150)
        kpi.record_retry("research")
        kpi.record_feedback_loop()
        self.assertIsNone(kpi.current())

    def test_accumulates_usage_across_calls(self):
        with kpi.track_run("run-1", "Acme") as kpis:
            kpi.record_llm_usage("gpt-4o-mini", 1000, 500, 1500)
            kpi.record_llm_usage("gpt-4o-mini", 800, 200, 1000)

        self.assertEqual(kpis.llm_calls, 2)
        self.assertEqual(kpis.prompt_tokens, 1800)
        self.assertEqual(kpis.completion_tokens, 700)
        self.assertEqual(kpis.total_tokens, 2500)
        self.assertAlmostEqual(kpis.estimated_cost_usd, (1800 * 0.15 + 700 * 0.60) / 1e6)

    def test_counts_retries_and_feedback_loops(self):
        with kpi.track_run("run-1") as kpis:
            kpi.record_retry("research")
            kpi.record_retry("research")
            kpi.record_feedback_loop()

        self.assertEqual(kpis.step_retries, 2)
        self.assertEqual(kpis.feedback_loops, 1)

    def test_step_durations_accumulate_per_step(self):
        with kpi.track_run("run-1") as kpis:
            kpi.record_step_duration("marketing", 1.5)
            kpi.record_step_duration("marketing", 2.0)

        self.assertAlmostEqual(kpis.step_durations["marketing"], 3.5)

    def test_context_is_restored_after_the_block(self):
        with kpi.track_run("run-1"):
            self.assertEqual(kpi.current().run_id, "run-1")
        self.assertIsNone(kpi.current())

    def test_context_is_restored_even_when_the_run_raises(self):
        with self.assertRaises(RuntimeError):
            with kpi.track_run("run-1"):
                raise RuntimeError("pipeline blew up")
        self.assertIsNone(kpi.current())

    def test_totals_are_serializable_for_the_store(self):
        with kpi.track_run("run-1") as kpis:
            kpi.record_llm_usage("gpt-4o-mini", 100, 50, 150)

        totals = kpis.totals()
        self.assertEqual(totals["llm_calls"], 1)
        self.assertEqual(totals["total_tokens"], 150)
        self.assertIn("estimated_cost_usd", totals)


class SummarizeTests(unittest.TestCase):
    def test_empty_input_returns_zeroed_summary(self):
        summary = kpi.summarize([])
        self.assertEqual(summary["runs"], 0)
        self.assertEqual(summary["success_rate"], 0.0)

    def test_aggregates_outcomes_and_rates(self):
        runs = [
            {"status": "completed", "total_tokens": 1000, "estimated_cost_usd": 0.001,
             "duration_seconds": 10.0, "feedback_loops": 0},
            {"status": "completed", "total_tokens": 2000, "estimated_cost_usd": 0.002,
             "duration_seconds": 20.0, "feedback_loops": 2},
            {"status": "incomplete", "total_tokens": 500, "estimated_cost_usd": 0.0005,
             "duration_seconds": 5.0, "feedback_loops": 0},
            {"status": "failed", "total_tokens": 0, "estimated_cost_usd": 0.0,
             "duration_seconds": None, "feedback_loops": 0},
        ]

        summary = kpi.summarize(runs)
        self.assertEqual(summary["runs"], 4)
        self.assertEqual(summary["completed"], 2)
        self.assertEqual(summary["incomplete"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["success_rate"], 0.5)
        self.assertEqual(summary["feedback_loop_rate"], 0.25)
        self.assertEqual(summary["total_tokens"], 3500)
        # The failed run has no duration and must not drag the average to zero.
        self.assertEqual(summary["avg_duration_seconds"], round(35.0 / 3, 3))

    def test_handles_missing_numeric_fields(self):
        summary = kpi.summarize([{"status": "completed"}])
        self.assertEqual(summary["total_tokens"], 0)
        self.assertEqual(summary["success_rate"], 1.0)



class TokenBudgetTests(unittest.TestCase):
    """A pathological run must not spend up to the tool-iteration ceiling."""

    def test_uncapped_by_default(self):
        with kpi.track_run("run-1") as kpis:
            kpi.record_llm_usage("gpt-4o-mini", 10_000_000, 10_000_000, 20_000_000)
            kpi.check_budget()  # must not raise
        self.assertEqual(kpis.token_budget, 0)

    def test_budget_stops_the_run_once_spent(self):
        with self.assertRaises(kpi.BudgetExceededError):
            with kpi.track_run("run-1", token_budget=1000):
                kpi.record_llm_usage("gpt-4o-mini", 600, 500, 1100)
                kpi.check_budget()

    def test_budget_allows_calls_below_the_ceiling(self):
        with kpi.track_run("run-1", token_budget=1000):
            kpi.record_llm_usage("gpt-4o-mini", 100, 50, 150)
            kpi.check_budget()  # must not raise

    def test_budget_error_is_not_an_llm_error(self):
        """It must escape `except LLMError` and the step retry, or retrying
        would spend more of a budget that is already gone."""
        from lib.llm import LLMError

        self.assertFalse(issubclass(kpi.BudgetExceededError, LLMError))

    def test_check_budget_is_a_noop_outside_a_run(self):
        self.assertIsNone(kpi.current())
        kpi.check_budget()

    def test_budget_read_from_environment(self):
        with patch.dict(os.environ, {"COA_TOKEN_BUDGET": "5000"}):
            self.assertEqual(kpi.default_token_budget(), 5000)

    def test_malformed_budget_env_runs_uncapped_rather_than_crashing(self):
        with patch.dict(os.environ, {"COA_TOKEN_BUDGET": "not-a-number"}):
            self.assertEqual(kpi.default_token_budget(), 0)

if __name__ == "__main__":
    unittest.main()
