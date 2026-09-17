import unittest

from tests.external_stubs import install

install()

from evals import cases as eval_cases
from evals.cases import LABEL_REVIEW, LABEL_SEND, CASES
from evals.harness import (
    decide,
    format_report,
    score_case,
    summarize,
    threshold_advice,
)


class CorpusTests(unittest.TestCase):
    """Guards the corpus itself. A rotted corpus fails silently — the evals keep
    passing while measuring nothing."""

    def test_corpus_is_not_empty_and_covers_both_labels(self):
        self.assertGreaterEqual(len(CASES), 6)
        self.assertTrue(eval_cases.cases_by_label(LABEL_SEND))
        self.assertTrue(eval_cases.cases_by_label(LABEL_REVIEW))

    def test_case_ids_are_unique(self):
        ids = [c.case_id for c in CASES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_labels_are_valid(self):
        for case in CASES:
            with self.subTest(case=case.case_id):
                self.assertIn(case.label, (LABEL_SEND, LABEL_REVIEW))

    def test_every_case_has_the_briefs_the_evaluator_needs(self):
        for case in CASES:
            with self.subTest(case=case.case_id):
                self.assertTrue(case.research.get("profile"))
                self.assertTrue(case.marketing.get("hook"))
                self.assertTrue(case.sales.get("email", {}).get("body"))

    def test_every_case_records_why_it_is_labelled_that_way(self):
        """An unexplained label is unmaintainable — nobody can tell later whether
        a disagreement means the model is wrong or the label is."""
        for case in CASES:
            with self.subTest(case=case.case_id):
                self.assertTrue(case.rationale.strip())

    def test_review_cases_name_their_failure_mode(self):
        for case in eval_cases.cases_by_label(LABEL_REVIEW):
            with self.subTest(case=case.case_id):
                self.assertTrue(case.failure_mode.strip())

    def test_review_cases_cover_distinct_failure_modes(self):
        tags = {tag for c in eval_cases.cases_by_label(LABEL_REVIEW) for tag in c.tags}
        for expected in ("generic", "hallucination", "multi-cta", "no-cta", "tone"):
            with self.subTest(tag=expected):
                self.assertIn(expected, tags)


class DecideTests(unittest.TestCase):
    """`decide` runs the real routing gate, so these also pin the thresholds."""

    def test_strong_evaluation_decides_send(self):
        self.assertEqual(decide({"overall": 9, "verdict": "approve"}), LABEL_SEND)

    def test_mediocre_evaluation_decides_review(self):
        self.assertEqual(decide({"overall": 5, "verdict": "approve"}), LABEL_REVIEW)

    def test_escalate_verdict_decides_review_even_when_scored_high(self):
        self.assertEqual(decide({"overall": 9, "verdict": "escalate"}), LABEL_REVIEW)


class ScoringTests(unittest.TestCase):
    def _case(self, label):
        return next(c for c in CASES if c.label == label)

    def test_false_approval_is_detected(self):
        """Labelled review, model approved it — the failure that costs something."""
        result = score_case(self._case(LABEL_REVIEW), {"overall": 9, "verdict": "approve"})

        self.assertTrue(result.is_false_approval)
        self.assertFalse(result.is_false_review)
        self.assertFalse(result.agreed)

    def test_false_review_is_detected_separately(self):
        result = score_case(self._case(LABEL_SEND), {"overall": 2, "verdict": "revise"})

        self.assertTrue(result.is_false_review)
        self.assertFalse(result.is_false_approval)

    def test_agreement_is_recorded(self):
        result = score_case(self._case(LABEL_SEND), {"overall": 9, "verdict": "approve"})
        self.assertTrue(result.agreed)

    def test_errored_case_is_neither_agreement_nor_false_anything(self):
        result = score_case(self._case(LABEL_SEND), {}, error="LLM call failed")

        self.assertFalse(result.agreed)
        self.assertFalse(result.is_false_approval)
        self.assertFalse(result.is_false_review)


class SummaryTests(unittest.TestCase):
    def _results(self):
        send = next(c for c in CASES if c.label == LABEL_SEND)
        review = next(c for c in CASES if c.label == LABEL_REVIEW)
        return [
            score_case(send, {"overall": 9, "verdict": "approve"}),
            score_case(review, {"overall": 3, "verdict": "revise"}),
            score_case(review, {"overall": 9, "verdict": "approve"}),
        ]

    def test_counts_and_agreement(self):
        summary = summarize(self._results())

        self.assertEqual(summary.total, 3)
        self.assertEqual(summary.agreed, 2)
        self.assertEqual(summary.false_approvals, 1)
        self.assertAlmostEqual(summary.agreement, 2 / 3, places=3)

    def test_a_single_false_approval_fails_by_default(self):
        """Zero tolerance in that direction is the whole point of the gate."""
        self.assertFalse(summarize(self._results()).passed())

    def test_clean_run_passes(self):
        send = next(c for c in CASES if c.label == LABEL_SEND)
        review = next(c for c in CASES if c.label == LABEL_REVIEW)
        summary = summarize([
            score_case(send, {"overall": 9, "verdict": "approve"}),
            score_case(review, {"overall": 3, "verdict": "revise"}),
        ])

        self.assertTrue(summary.passed())

    def test_errors_fail_the_run_rather_than_being_ignored(self):
        send = next(c for c in CASES if c.label == LABEL_SEND)
        summary = summarize([score_case(send, {}, error="boom")])

        self.assertEqual(summary.errors, 1)
        self.assertFalse(summary.passed())

    def test_score_ranges_are_reported_per_label(self):
        summary = summarize(self._results())

        self.assertEqual(summary.score_ranges[LABEL_SEND]["n"], 1)
        self.assertEqual(summary.score_ranges[LABEL_REVIEW]["max"], 9)

    def test_threshold_advice_flags_overlap(self):
        summary = summarize(self._results())
        self.assertIn("Overlap", threshold_advice(summary))

    def test_threshold_advice_reports_clean_separation(self):
        send = next(c for c in CASES if c.label == LABEL_SEND)
        review = next(c for c in CASES if c.label == LABEL_REVIEW)
        summary = summarize([
            score_case(send, {"overall": 9, "verdict": "approve"}),
            score_case(review, {"overall": 3, "verdict": "revise"}),
        ])

        self.assertIn("Clean separation", threshold_advice(summary))

    def test_report_names_false_approvals_prominently(self):
        report = format_report(summarize(self._results()))

        self.assertIn("FALSE-APPROVE", report)
        self.assertIn("false approvals", report)


if __name__ == "__main__":
    unittest.main()
