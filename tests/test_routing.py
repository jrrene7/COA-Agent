import unittest

from tests.external_stubs import install

install()

from lib import routing
from lib.routing import route_inbound, route_outbound


class NormalizationTests(unittest.TestCase):
    def test_sentiment_variants_map_to_the_vocabulary(self):
        """Models return 'Very Negative' / 'NEG' / 'negative' for the same thing;
        an exact-match lookup would silently stop escalating."""
        for raw in ("negative", "Negative", "VERY NEGATIVE", "very_negative", "  negative  "):
            with self.subTest(raw=raw):
                self.assertEqual(routing.normalize_sentiment(raw), routing.SENTIMENT_NEGATIVE)

    def test_unknown_sentiment_falls_back_to_neutral(self):
        self.assertEqual(routing.normalize_sentiment("sparkly"), routing.SENTIMENT_NEUTRAL)
        self.assertEqual(routing.normalize_sentiment(None), routing.SENTIMENT_NEUTRAL)

    def test_intent_variants_map_to_the_vocabulary(self):
        self.assertEqual(routing.normalize_intent("Churn Risk"), routing.INTENT_CHURN_RISK)
        self.assertEqual(routing.normalize_intent("churn-risk"), routing.INTENT_CHURN_RISK)
        self.assertEqual(routing.normalize_intent("wat"), routing.INTENT_OTHER)

    def test_escalate_priority_saturates_at_urgent(self):
        self.assertEqual(routing.escalate_priority("low"), routing.PRIORITY_NORMAL)
        self.assertEqual(routing.escalate_priority("high"), routing.PRIORITY_URGENT)
        self.assertEqual(routing.escalate_priority("urgent"), routing.PRIORITY_URGENT)


class RouteOutboundTests(unittest.TestCase):
    def test_strong_draft_on_strong_fit_goes_to_the_priority_queue(self):
        decision = route_outbound(
            {"overall": 9, "verdict": "approve"}, marketing_data={"icp_fit": "9 - strong fit"}
        )
        self.assertEqual(decision.queue, routing.QUEUE_PRIORITY)
        self.assertFalse(decision.escalate)

    def test_strong_draft_on_weak_fit_goes_to_the_standard_queue(self):
        decision = route_outbound(
            {"overall": 8, "verdict": "approve"}, marketing_data={"icp_fit": "3"}
        )
        self.assertEqual(decision.queue, routing.QUEUE_STANDARD)
        self.assertFalse(decision.escalate)

    def test_explicit_escalate_verdict_escalates(self):
        decision = route_outbound({"overall": 9, "verdict": "escalate"})
        self.assertTrue(decision.escalate)
        self.assertEqual(decision.queue, routing.QUEUE_HUMAN_REVIEW)

    def test_very_poor_draft_escalates_regardless_of_verdict(self):
        decision = route_outbound({"overall": 2, "verdict": "approve"})
        self.assertTrue(decision.escalate)

    def test_mediocre_draft_goes_to_human_review_not_out_the_door(self):
        """A bad cold email is more expensive than a delayed one."""
        decision = route_outbound({"overall": 6, "verdict": "approve"})
        self.assertEqual(decision.queue, routing.QUEUE_HUMAN_REVIEW)
        self.assertTrue(decision.escalate)

    def test_exhausted_attempts_are_reflected_in_the_reason(self):
        decision = route_outbound({"overall": 6, "verdict": "revise"}, attempts_exhausted=True)
        self.assertTrue(decision.escalate)
        self.assertIn("revision budget", decision.reason)

    def test_icp_fit_parses_from_prose(self):
        """icp_fit arrives as '8 - strong fit' as often as it does as 8."""
        decision = route_outbound(
            {"overall": 9, "verdict": "approve"}, marketing_data={"icp_fit": "8 - strong fit"}
        )
        self.assertEqual(decision.queue, routing.QUEUE_PRIORITY)

    def test_missing_evaluation_does_not_silently_approve(self):
        decision = route_outbound({})
        self.assertTrue(decision.escalate)

    def test_every_decision_carries_a_reason(self):
        for evaluation in ({"overall": 9, "verdict": "approve"}, {"overall": 1}, {}):
            with self.subTest(evaluation=evaluation):
                self.assertTrue(route_outbound(evaluation).reason)


class RouteInboundTests(unittest.TestCase):
    def test_angry_message_escalates_urgently(self):
        decision = route_inbound({"sentiment": "angry", "intent": "complaint"})
        self.assertEqual(decision.queue, routing.QUEUE_ESCALATION)
        self.assertEqual(decision.priority, routing.PRIORITY_URGENT)
        self.assertTrue(decision.escalate)

    def test_politely_worded_churn_risk_still_escalates(self):
        """Intent outranks tone: a friendly cancellation is still a cancellation."""
        decision = route_inbound(
            {"sentiment": "positive", "intent": "churn_risk", "urgency": "low"}
        )
        self.assertTrue(decision.escalate)
        self.assertEqual(decision.queue, routing.QUEUE_ESCALATION)

    def test_unsubscribe_always_escalates(self):
        decision = route_inbound({"sentiment": "neutral", "intent": "unsubscribe"})
        self.assertTrue(decision.escalate)

    def test_frustrated_question_is_prioritised_but_not_escalated(self):
        """A frustrated question is still just a question."""
        decision = route_inbound(
            {"sentiment": "negative", "intent": "question", "urgency": "normal"}
        )
        self.assertEqual(decision.queue, routing.QUEUE_PRIORITY)
        self.assertFalse(decision.escalate)
        self.assertEqual(decision.priority, routing.PRIORITY_HIGH)

    def test_routine_question_goes_to_the_standard_queue(self):
        decision = route_inbound(
            {"sentiment": "neutral", "intent": "question", "urgency": "normal"}
        )
        self.assertEqual(decision.queue, routing.QUEUE_STANDARD)
        self.assertFalse(decision.escalate)

    def test_urgent_flag_raises_a_neutral_message(self):
        decision = route_inbound(
            {"sentiment": "neutral", "intent": "question", "urgency": "urgent"}
        )
        self.assertEqual(decision.queue, routing.QUEUE_PRIORITY)

    def test_empty_triage_routes_somewhere_safe(self):
        decision = route_inbound({})
        self.assertEqual(decision.queue, routing.QUEUE_STANDARD)
        self.assertTrue(decision.reason)

    def test_decision_serializes_for_state(self):
        decision = route_inbound({"sentiment": "angry"})
        as_dict = decision.to_dict()
        self.assertEqual(
            set(as_dict), {"queue", "priority", "owner", "escalate", "reason"}
        )

class SendableTests(unittest.TestCase):
    """The single field an integration should branch on."""

    def test_approved_routing_is_sendable(self):
        decision = route_outbound({"overall": 9, "verdict": "approve"})
        self.assertTrue(routing.sendable(decision.to_dict()))

    def test_escalated_routing_is_not_sendable(self):
        decision = route_outbound({"overall": 2, "verdict": "revise"})
        self.assertFalse(routing.sendable(decision.to_dict()))

    def test_missing_routing_is_not_sendable(self):
        """Never reviewed is not the same as approved — fail closed."""
        self.assertFalse(routing.sendable({}))
        self.assertFalse(routing.sendable(None))

    def test_routing_without_an_escalate_key_fails_closed(self):
        self.assertFalse(routing.sendable({"queue": "standard"}))


class FrontMatterTests(unittest.TestCase):
    def test_declares_sendable_false_when_escalated(self):
        decision = route_outbound({"overall": 3, "verdict": "revise"})
        fm = routing.front_matter("run-1", "outbound", decision.to_dict(), 3)

        self.assertIn("sendable: false", fm)
        self.assertIn("escalate: true", fm)
        self.assertIn("queue: human_review", fm)
        self.assertIn("quality_score: 3", fm)

    def test_declares_sendable_true_when_approved(self):
        decision = route_outbound({"overall": 9, "verdict": "approve"})
        fm = routing.front_matter("run-1", "outbound", decision.to_dict(), 9)

        self.assertIn("sendable: true", fm)
        self.assertIn("escalate: false", fm)

    def test_is_delimited_yaml_front_matter(self):
        fm = routing.front_matter("run-1", "inbound", {})
        lines = fm.splitlines()
        self.assertEqual(lines[0], "---")
        self.assertEqual(lines[-1], "---")
        self.assertIn("direction: inbound", fm)

    def test_empty_routing_fails_closed(self):
        self.assertIn("sendable: false", routing.front_matter("run-1", "inbound", {}))

    def test_quality_score_omitted_when_absent(self):
        self.assertNotIn("quality_score", routing.front_matter("run-1", "inbound", {}))


if __name__ == "__main__":
    unittest.main()
