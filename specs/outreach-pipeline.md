# Outreach Pipeline Specification

## Goal
Build a customer outreach automation agent that turns a sales lead into a structured Markdown report containing company research, marketing strategy, sales outreach, objection handling, and a recommended next action.

## Actors
- Sales operator: runs the pipeline for one or more leads and reads generated reports.
- Lead company: the target organization being researched.
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
