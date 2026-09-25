# Project Constitution

This project follows spec-driven development. The specification is the source of intent, and implementation changes must remain traceable to that intent.

## Architecture
- Two pipelines: outbound lead outreach is coordinated by `agents.orchestrator.Orchestrator`; inbound message triage by `agents.inbound_orchestrator.InboundOrchestrator`.
- Specialist agents must keep separate responsibilities: research gathers facts, marketing creates positioning, sales creates outreach, evaluation judges draft quality, triage classifies inbound messages, reply drafts responses, and the writers format output.
- Shared workflow behavior belongs in `lib/`; external integrations belong in `tools/`.
- Agent steps communicate through `OutreachState` or `InboundState` and must return dictionaries that merge into workflow state.
- Workflow routing should use LangGraph rather than project-specific orchestration code.
- LLM prompts and tool responses should use LangChain message primitives.
- Agents produce signals; routing decisions are made deterministically in `lib/routing.py`. A model must not decide a queue, a priority, or whether to escalate.
- Every run is persisted durably by `run_id` before the process exits.

## Human oversight
- No generated message is ever sent automatically. Outbound drafts are queued or escalated; inbound replies are drafts; escalated inbound messages get no draft at all.
- Generated documents must carry machine-readable front matter declaring `sendable`, so an integration can honour the review gate without parsing prose.
- Every escalation records an owner and an SLA due time, and whether a human responded.
- Quality gates that depend on a model must have evals. A gate nobody measures is a gate that can regress silently.

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
- Only `http` and `https` URLs may be scraped, and only at public addresses — loopback, link-local, private, and reserved ranges are rejected, including across redirects.
- External text must be fenced with `wrap_untrusted` before it enters a prompt, including prior agent output derived from it.
- Generated report filenames must be sanitized and confined to the `output/` directory.
- Logs must redact API keys, bearer tokens, passwords, and common secret fields.
- Logs must avoid storing full prompts, tool outputs, report content, or raw API responses.

## Reliability
- External LLM calls must use bounded retry behavior.
- Tool loops must have maximum iteration counts.
- Every loop must have a ceiling, and the ceiling must be a named constant.
- Runs must honour a per-run token budget when one is configured.
- Any batch processing workflow must continue after one lead fails and record the failure.
- Errors should include enough context to diagnose the failed pipeline step.
- Workflow logs should record step starts/completions and high-level metadata only.

## Testing
- Business logic requires automated unit tests.
- Unit tests must not require real OpenAI or Tavily API calls.
- Tests should cover validation, graph workflow execution, message handling, report generation, filesystem safety, logging redaction, routing and escalation rules, and durable persistence.
- Model-dependent quality gates are measured by `evals/`, which makes real API calls and therefore runs outside the unit suite.

## Change Control
- Requirement changes update the SDD spec before implementation.
- Architecture or dependency changes update the plan before code changes.
- Tasks should remain small, testable, and traceable to requirements.
