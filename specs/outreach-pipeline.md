# Outreach Pipeline Specification

## Goal
Build a customer outreach automation agent with two pipelines:

- **Outbound** — turn a sales lead into a structured Markdown report containing company research, marketing strategy, sales outreach, objection handling, a recommended next action, a quality review, and a routing decision.
- **Inbound** — turn a customer message into a triaged, routed note that is either a reply draft for human review or an escalation handoff.

No generated message is ever sent automatically. The system's output is always something a human approves.

## Actors
- Sales operator: runs the outbound pipeline for one or more leads and reads generated reports.
- Lead company: the target organization being researched.
- Customer: sends an inbound message (reply, ticket, or form submission).
- Reviewer / owner: the human a draft or escalation is routed to, who approves, edits, or responds within the SLA.
- External services: OpenAI provides LLM reasoning, Tavily provides web search, and public websites provide company information.

## Scope
The system supports local command-line execution for single-lead workflows in this branch. It does not send emails, write to a CRM, schedule meetings, or provide a web interface.

## Functional Requirements
- R-01: The system accepts a company name and optional website URL for each lead.
- R-02: The system rejects an empty company name.
- R-03: The system rejects non-empty URLs that are not valid `http` or `https` URLs.
- R-04: The research agent gathers company profile, recent news, people, technology stack, pain points, and sources.
- R-05: The marketing agent produces ICP fit, value propositions, tone, hook, competitors, and positioning from research data.
- R-06: The sales agent produces a cold email, LinkedIn note, follow-up sequence, objections, and next action from research and marketing data.
- R-07: The report writer creates a Markdown report in `output/` using sanitized filenames.
- R-08: The orchestrator runs steps in this order: research, marketing, sales, report.
- R-09: A completed run exposes final state and per-step snapshots.
- R-10: The orchestrator uses LangGraph to manage workflow routing and state updates.
- R-11: Agent and LLM message handling uses LangChain message primitives.
- R-12: Application logging redacts secrets and emits workflow-safe metadata.

## Non-Functional Requirements
- NFR-01: Unit tests run offline without API keys.
- NFR-02: Tool and LLM loops are bounded to prevent runaway execution.
- NFR-03: External failures are converted into clear domain-specific errors.
- NFR-04: Report output is deterministic for a given completed pipeline state, except for the generation date.
- NFR-05: Source code remains small enough for classroom review and project submission.
- NFR-06: Unit tests verify logging redaction without writing secrets to stdout or stderr.

## Edge Cases
- Empty or whitespace-only company names are invalid.
- URLs with missing hosts, non-web schemes, or malformed values are invalid.
- Missing optional research, marketing, or sales fields render as clear fallback text in the report.
- Invalid LLM JSON responses fall back to partial content instead of crashing where possible.

## Acceptance Criteria
- AC-01: Calling `Orchestrator.run()` with an empty company raises `OrchestratorError`.
- AC-02: Calling `Orchestrator.run()` with an invalid URL raises `OrchestratorError`.
- AC-03: A valid orchestrator run executes the four pipeline steps in order and returns final report state.
- AC-04: `write_report` never writes outside `output/`, even when given unsafe filenames.
- AC-05: `ReportWriter.write()` creates the expected report sections from supplied state.
- AC-06: Unit tests can be run with `python -m unittest discover` and do not require API credentials.
- AC-07: The orchestrator builds a LangGraph `StateGraph` with research, marketing, sales, and report nodes.
- AC-08: LLM request messages use LangChain system, human, AI, and tool message objects.
- AC-09: Logs redact API keys, bearer tokens, and password-like values before emission.


## Added requirements (2026-09)

These were implemented after the original specification and are recorded here to
keep the spec traceable to the code, per the constitution's change-control rule.

| ID | Requirement |
|---|---|
| R-20 | The outbound pipeline must score every draft for quality and sentiment before it is written up. |
| R-21 | A rejected draft must be returned to the marketing step carrying the reviewer's critique, bounded by a named retry ceiling. |
| R-22 | Routing must be deterministic: a model may produce signals, but may not choose a queue, priority, owner, or escalation. |
| R-23 | A draft that does not pass review must be routed to a human, not presented as sendable. |
| R-24 | The inbound pipeline must classify sentiment, intent, and urgency, and route on intent ahead of tone. |
| R-25 | Escalated inbound messages must not receive an auto-drafted reply. |
| R-26 | Generated documents must carry machine-readable front matter declaring `sendable`. |
| R-27 | Every run must be persisted durably, keyed by `run_id`, and reconstructable after the process exits. |
| R-28 | Every escalation must record an owner, an SLA due time, and whether a human responded. |
| R-29 | Runs must honour a per-run token budget when one is configured. |
| R-30 | Scraping must reject non-public addresses, including across redirects. |
| R-31 | The model-dependent quality gate must be measurable against a labelled corpus. |

### Acceptance criteria

- AC-20: An evaluator verdict of `revise` routes back to marketing and the next marketing call receives a non-empty critique.
- AC-21: A draft scoring below the approve threshold produces `routing.escalate == true` and a report whose front matter declares `sendable: false`.
- AC-22: A politely worded `churn_risk` inbound message escalates despite positive sentiment.
- AC-23: An escalated inbound run has an empty `reply` and a populated `handoff`.
- AC-24: A crash mid-pipeline leaves the completed steps recoverable from the run store by `run_id`.
- AC-25: `mark_responded` on an unknown or already-answered run reports no change rather than silently succeeding.
- AC-26: A run exceeding its token budget raises before the next model call, and the step retry does not re-spend.
- AC-27: A URL resolving to a private or link-local address is rejected, as is a public URL that redirects to one.
