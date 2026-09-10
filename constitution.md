# Project Constitution

This project follows spec-driven development. The specification is the source of intent, and implementation changes must remain traceable to that intent.

## Architecture
- The outreach workflow is coordinated by `agents.orchestrator.Orchestrator`.
- Specialist agents must keep separate responsibilities: research gathers facts, marketing creates positioning, sales creates outreach, and report writing formats output.
- Shared workflow behavior belongs in `lib/`; external integrations belong in `tools/`.
- Agent steps communicate through `OutreachState` and must return dictionaries that merge into workflow state.
- Workflow routing should use LangGraph rather than project-specific orchestration code.
- LLM prompts and tool responses should use LangChain message primitives.

## Technology
- Use Python for source code.
- Keep the runtime dependency set small and explicit in `requirements.txt`.
- LangGraph is approved for graph-based workflow and state management.
- LangChain Core is approved for message primitives.
- Use Markdown for generated lead reports and project documentation.
- Avoid adding new frameworks unless a requirement cannot be met with the current structure.

## Security
- Never hard-code API keys or secrets.
- Load secrets from `config.env` or environment variables.
- Validate external input before using it for network or filesystem operations.
- Only `http` and `https` URLs may be scraped.
- Generated report filenames must be sanitized and confined to the `output/` directory.
- Logs must redact API keys, bearer tokens, passwords, and common secret fields.
- Logs must avoid storing full prompts, tool outputs, report content, or raw API responses.

## Reliability
- External LLM calls must use bounded retry behavior.
- Tool loops must have maximum iteration counts.
- Any batch processing workflow must continue after one lead fails and record the failure.
- Errors should include enough context to diagnose the failed pipeline step.
- Workflow logs should record step starts/completions and high-level metadata only.

## Testing
- Business logic requires automated unit tests.
- Unit tests must not require real OpenAI or Tavily API calls.
- Tests should cover validation, graph workflow execution, message handling, report generation, filesystem safety, and logging redaction.

## Change Control
- Requirement changes update the SDD spec before implementation.
- Architecture or dependency changes update the plan before code changes.
- Tasks should remain small, testable, and traceable to requirements.
