# Outreach Pipeline Plan

## Architecture
The system uses a sequential LangGraph `StateGraph` architecture.

- `main.py` handles single-lead command-line execution.
- `agents/orchestrator.py` validates leads, owns specialist agent instances, and wires the LangGraph workflow.
- `agents/*_agent.py` modules perform research, marketing, and sales specialist work.
- `agents/report_writer.py` formats completed state into Markdown.
- `lib/workflow.py` provides lightweight run and snapshot result objects for callers.
- `lib/messages.py` exposes LangChain message primitives and normalizes tool calls.
- `lib/logging_config.py` configures secure, redacted logging.
- `tools/web_tools.py` and `tools/file_tools.py` isolate external side effects.

## Data
Pipeline state is represented by `OutreachState`.

- `lead`: input company and URL.
- `research_data`: structured research output.
- `marketing_data`: positioning and messaging output.
- `sales_data`: generated outreach output.
- `report_path`: absolute path to the generated Markdown report.

## Interfaces
- Public library API: `Orchestrator.run(company: str, url: str = "") -> Run`.
- Report writer API: `ReportWriter.write(state: OutreachState) -> str`.
- Tool APIs: `web_search(query, max_results)`, `scrape_website(url)`, and `write_report(filename, content)`.
- Logging API: `configure_logging()` installs a redacting filter for application and third-party loggers.

## Algorithm And Data Structure Decisions
- The workflow is a linear LangGraph because the required lead-generation process has a fixed dependency order.
- LangGraph streaming updates are converted into ordered snapshots because the CLI and README expose step history.
- Filename sanitization uses a conservative regular expression because output names are short, human-facing, and do not require complex parsing.
- LangChain Core message objects replace local message dataclasses so prompt, AI, and tool messages use a maintained schema.

## Security
- `Orchestrator` validates company and URL inputs before any agent step runs.
- `scrape_website` only accepts `http` and `https`.
- `write_report` sanitizes filenames, resolves the final path, and confirms output remains under `output/`.
- API keys are read from the environment and never stored in source code.
- Logging uses a redacting filter for common secret patterns and does not log prompt bodies, full API responses, or generated report content.

## Reliability
- LLM calls retry transient rate-limit and timeout errors.
- Agent tool-call loops are capped.
- LangGraph nodes return state-update dictionaries and graph streaming preserves ordered step snapshots.
- Third-party HTTP/OpenAI logs are set to warning by default to reduce accidental payload exposure.

## Testing Strategy
- Unit test lead validation without constructing real LLM clients.
- Unit test LangGraph workflow sequencing with fake specialist agents.
- Unit test LangChain message serialization and tool-call normalization.
- Unit test report formatting with a monkeypatched file writer.
- Unit test filename sanitization using a temporary output directory.
- Unit test logging redaction.

## Traceability Matrix
| Requirement | Plan Area | Tasks | Code | Tests |
|---|---|---|---|---|
| R-01, R-02, R-03 | Interfaces, Security | T-03 | `agents/orchestrator.py` | `tests/test_orchestrator.py` |
| R-04, R-05, R-06 | Architecture | T-01 | `agents/research_agent.py`, `agents/marketing_agent.py`, `agents/sales_agent.py` | Existing source review |
| R-07 | Data, Security | T-04 | `agents/report_writer.py`, `tools/file_tools.py` | `tests/test_report_writer.py`, `tests/test_file_tools.py` |
| R-08, R-09, R-10 | Architecture, Reliability | T-03 | `agents/orchestrator.py`, `lib/workflow.py` | `tests/test_orchestrator.py` |
| R-11 | Interfaces | T-07 | `lib/messages.py`, `lib/llm.py`, `agents/*_agent.py` | `tests/test_llm.py` |
| R-12, NFR-06 | Security | T-08 | `lib/logging_config.py`, `main.py` | `tests/test_logging_config.py` |
| NFR-01 | Testing Strategy | T-05 | `tests/` | `python -m unittest discover` |
| NFR-02, NFR-03 | Reliability | T-01 | `lib/llm.py`, `agents/*_agent.py` | Existing source review |
| NFR-04 | Data | T-04 | `agents/report_writer.py` | `tests/test_report_writer.py` |
