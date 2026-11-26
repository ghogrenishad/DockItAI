# 01 — Application foundation and service shell

**Status:** complete  
**Completed:** 2026-11-26  
**Owner:** Codex with GPT-5.6-Terra (High) implementation pass  
**Implementation-plan step:** Step 1 in docs/IMPLEMENTATION_PLAN.md

## Scope and acceptance criteria

Create a runnable, typed FastAPI shell without domain, scheduler, persistence,
agent, authentication, or AWS integration behaviour. The shell must load
validated configuration, expose versioned/liveness/readiness endpoints, attach
a correlation ID to each request, avoid leaking likely secrets in structured
request logs, and have automated tests.

The user explicitly authorized Step 1 while Step 0 remains in progress. This
was safe because the change does not encode unresolved booking, scheduling,
source-data, or integration decisions.

## Relevant context and decisions

- The user requested an educational implementation and required GPT-5.6-Terra
  at High reasoning effort. The implementation worker used that model.
- The Notion roadmap confirms Streamlit, Cognito, LangChain, Redis, CP-SAT,
  AgentCore, and the prohibition on LLM operational decisions. None is invoked
  by this step.
- The existing configuration used one database path even though the migration
  guidance requires the SetuHaul seed database to remain unchanged.
- Current readiness deliberately means configuration is valid and the app
  factory has started. It does not mean Redis, Cognito, AgentCore, AWS, or a
  database are reachable; those clients do not exist yet.

## Confirmed facts and assumptions

| Item | Status |
| --- | --- |
| Project runtime requirement | Confirmed: Python 3.14 only. |
| Project virtual environment | Confirmed: .venv uses Python 3.14.10 and imports FastAPI. |
| Initially selected editor environment | Observed: fresh .venv-1 used Python 3.15.2 and had only pip; it conflicts with project support. |
| Source and state data paths | Design decision: separate now, before persistence code exists. |
| Production Cognito/AWS/AgentCore variables | Design decision: validate their presence in production but make no external call. |
| Request timeout/body-size settings | Configuration only; enforcement is deferred to the appropriate API security step. |
| Liveness/readiness semantics | Design decision: process response versus loaded/validated configuration. |

## Design and contract impact

- Settings are typed with Pydantic and resolve relative paths from the project
  root. The legacy DOCKIT_DATABASE_PATH name remains a source-path alias for
  compatibility; DockIt-owned state uses DOCKIT_STATE_DATABASE_PATH.
- Source and state paths must differ, preventing a future write layer from
  accidentally treating the seed database as DockIt state.
- Production fails configuration validation without Cognito identifiers,
  AWS_REGION, and AGENTCORE_RUNTIME_ARN. Secrets use SecretStr and are never
  rendered by the request log formatter.
- The app factory accepts injected Settings for deterministic tests. The global
  ASGI entry point exists for Uvicorn and the dockit-api project script.
- Correlation IDs accept valid client UUIDs or generate a replacement UUID, then
  return the identifier in X-Correlation-ID and include it in request events.
- The public shell contains only GET /api/v1, GET /healthz, and GET /readyz.

## Changed artifacts

| Artifact | Purpose |
| --- | --- |
| pyproject.toml | Adds dockit-api entry point and a pytest asyncio loop-scope setting. |
| .env.example | Documents source/state separation and runtime/logging/server settings. |
| README.md | Documents shell operation and quality checks. |
| src/dockit/core/config.py | Typed configuration and production validation. |
| src/dockit/core/logging.py | JSON request formatter, correlation context, and recursive secret-key redaction. |
| src/dockit/api/app.py | Factory and resource lifecycle hook. |
| src/dockit/api/main.py | ASGI entry point and Uvicorn launcher. |
| src/dockit/api/middleware.py | Correlation-ID middleware and request-completion event. |
| src/dockit/api/routes/__init__.py | Routes package marker. |
| src/dockit/api/routes/system.py | Versioned-root, liveness, and readiness endpoints. |
| tests/test_config.py | Configuration/path/production-validation tests. |
| tests/test_system.py | Service shell and correlation-ID tests. |
| tests/test_logging.py | Nested secret-redaction test. |

## Tool and command ledger

| Tool/action | Sanitized command or action | Purpose | Result |
| --- | --- | --- | --- |
| Skill review | python-fact-grounded-coding and karpathy-guidelines | Ground changes in observable facts and keep scope narrow. | Applied. |
| GPT-5.6-Terra (High) worker | Step 1 implementation request | Implement the scoped service shell. | Completed; no domain or cloud integration added. |
| Python environment selection | configure project .venv interpreter | Align validation with project Python 3.14 support. | Python 3.14.10 selected; the initially selected 3.15.2 environment was unsuitable. |
| Environment inspection | inspect .venv, import FastAPI | Verify that runtime dependencies already existed. | FastAPI 0.165.0 imports successfully. |
| Package install attempt | install editable dev/UI dependencies | Check whether dependency setup was needed. | Installer reported failure; no dependency mutation was needed because the existing runtime works. |
| Runtime pip check | .venv Python pip install command | Attempt local editable install. | pip module is absent although application packages are present; no repair performed because it was unrelated to Step 1. |
| Implementation-worker checks | Ruff, mypy, pytest, app boot smoke | Validate implementation before hand-off. | Passed. |
| Parent review checks | Ruff check, mypy src, pytest -q | Independently validate code quality and tests. | Ruff passed; mypy passed on 16 files; pytest passed 8 tests. |
| Editor diagnostics | workspace problems check | Detect editor-visible issues after edits. | Pylance still reports unresolved pydantic and pydantic_settings imports, inconsistent with successful runtime/mypy tests; see limitations. |
| Git state | git status | Record repository state. | Unavailable: macOS command-line developer tools are not installed. |

## Validation evidence

- Ruff: All checks passed.
- Mypy: Success: no issues found in 16 source files.
- Pytest: 8 passed in 0.18 seconds.
- Pytest emitted 17 existing LangSmith deprecation warnings about
  asyncio.iscoroutinefunction and Python 3.16; no test failed.
- FastAPI import/boot smoke check: DockIt AI API 0.1.0.
- System tests verify API metadata, liveness, readiness, valid caller
  correlation IDs, and invalid-ID replacement.
- Configuration tests verify absolute path resolution, path separation, and
  production requirements.

## Security and operational impact

- No request contacts Redis, Cognito, AgentCore, AWS, or either database.
- Settings distinguish the future read-only source database from writable
  DockIt state before persistence work begins.
- Production configuration refuses to start when required identity/runtime
  values are absent.
- Request logs use a narrow, explicit event schema and redact values of fields
  whose names indicate authorization, credentials, passwords, secrets, or
  tokens.

## Limitations, risks, and follow-up decisions

- Pylance has two unresolved-import diagnostics for Pydantic in configuration,
  despite the configured Python 3.14 runtime importing FastAPI and passing
  Ruff, mypy, and pytest. Re-check its selected interpreter before treating
  this as an application defect.
- The project virtual environment has application packages but no importable
  pip module. Rebuild or repair it only if future dependency changes require
  installation; doing so is not necessary for this completed step.
- Request size and timeout settings are not yet enforced. Security middleware
  and rate limits belong to the later identity/protections step.
- Readiness intentionally does not probe external dependencies. Add those
  checks only with real clients and a documented degraded-mode policy.
- Source schema, booking authority, hold semantics, and scheduling objectives
  remain blocked on Step 0 decisions.

## Git state and artifacts

Git status could not be collected because this macOS environment lacks command
line developer tools. The changed-artifact table above is the reviewed file
inventory. There are no database migrations, external resources, or secrets
created by this step.

## Next step

Resolve the remaining Step 0 business decisions while preparing Step 2:
inspect only a safe copy of the SetuHaul seed database, document its
read-only query contract, and create the separate DockIt state migration
foundation. Do not write booking or external-integration code until authority
and hold semantics are confirmed.

