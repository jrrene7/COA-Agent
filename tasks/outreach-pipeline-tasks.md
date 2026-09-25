# Outreach Pipeline Tasks

Tasks are intentionally small, testable, ordered, and traceable to the specification.

| Task | Requirement | Status | Verification |
|---|---|---|---|
| T-01 Document current agent responsibilities and constraints | R-04, R-05, R-06, NFR-02, NFR-03 | Done | `constitution.md`, `plans/outreach-pipeline-plan.md` |
| T-02 Add SDD specification with functional requirements and acceptance criteria | All | Done | `specs/outreach-pipeline.md` |
| T-03 Keep lead validation and orchestrator sequencing testable without API keys | R-01, R-02, R-03, R-08, R-09 | Done | `tests/test_orchestrator.py` |
| T-04 Verify report formatting and filesystem safety | R-07, NFR-04 | Done | `tests/test_report_writer.py`, `tests/test_file_tools.py` |
| T-05 Add offline unit test suite and test command | NFR-01 | Done | `tests/`, README test section |
| T-06 Add architecture diagram for submission review | Minimum requirement: architecture diagram | Done | `docs/architecture.md` |
| T-07 Replace local message dataclasses with LangChain message primitives | R-11 | Done | `lib/messages.py`, `lib/llm.py`, `tests/test_llm.py` |
| T-08 Replace custom orchestration with LangGraph workflow execution | R-08, R-09, R-10 | Done | `agents/orchestrator.py`, `lib/workflow.py`, `tests/test_orchestrator.py` |
| T-09 Add secure redacted logging configuration | R-12, NFR-06 | Done | `lib/logging_config.py`, `main.py`, `tests/test_logging_config.py` |

## Future Tasks
- T-10 Bring the `main_batch.py` workflow from `main` into this branch when the branches are integrated.
- T-11 Add integration tests with mocked OpenAI and Tavily client responses.
- T-12 Add a FastAPI wrapper if the project needs a web submission path.
- T-13 Add email or CRM integrations only after updating the spec and plan.

## Follow-on tasks (2026-09)

| Task | Requirement | Status | Verification |
|---|---|---|---|
| T-20 Add evaluator agent and quality gate | R-20, R-23 | Done | `tests/test_evaluator_agent.py` |
| T-21 Carry critique into the marketing retry | R-21 | Done | `tests/test_orchestrator.py::test_evaluator_critique_reaches_the_marketing_retry` |
| T-22 Add deterministic routing layer | R-22, R-24 | Done | `tests/test_routing.py` |
| T-23 Add inbound triage pipeline | R-24, R-25 | Done | `tests/test_inbound_orchestrator.py` |
| T-24 Add machine-readable front matter | R-26 | Done | `tests/test_routing.py::FrontMatterTests` |
| T-25 Add durable run store keyed by run_id | R-27 | Done | `tests/test_persistence.py` |
| T-26 Add SLA tracking on escalations | R-28 | Done | `tests/test_persistence.py::SlaTrackingTests` |
| T-27 Add per-run token budget | R-29 | Done | `tests/test_kpi.py::TokenBudgetTests` |
| T-28 Add SSRF protection to scraping | R-30 | Done | `tests/test_web_tools.py::SsrfTests` |
| T-29 Add evaluator eval corpus and harness | R-31 | Done | `tests/test_evals.py`; live run via `python -m evals.run` |
| T-30 Calibrate approve/escalate thresholds against the real model | R-31 | **Blocked** | Needs `OPENAI_API_KEY` and a spend allowance |
| T-31 Add inbound conversation/thread state | — | Open | Not started; each message is triaged in isolation |
| T-32 Replace SQLite when parallel workers are needed | — | Deferred | Documented constraint in `docs/deployment.md` |

