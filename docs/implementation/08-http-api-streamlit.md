# 08 — HTTP API and Streamlit pilot UI

**Status:** complete for the authenticated local/API/UI boundary  
**Completed:** 2026-11-27  
**Owner:** Codex  
**Implementation model:** GPT-5.6-Terra (High)  
**Documentation model:** GPT-5.6-Luna (High)  
**Implementation-plan step:** Step 8 in `docs/IMPLEMENTATION_PLAN.md`

## Scope, roadmap items, and acceptance criteria

Expose the confirmed journeys through versioned FastAPI routes and deliver the
roadmap-selected Streamlit pilot as an HTTP client. The API must enforce
authenticated driver ownership, use typed service boundaries, return stable
errors, support pagination and idempotent confirmation, and publish OpenAPI
contracts. The UI must not reproduce scheduling, authorization, or booking
policy.

## Relevant chat context and confirmed decisions

- Steps 1–7 were complete before this step began.
- The user authorized proceeding with Step 8 and requested that the established
  implementation/documentation/planning/review model preferences remain in
  force.
- The service layer remains authoritative. Routes call `SourceRepository`,
  `OperationalWorkflow`, `BookingWorkflow`, and the Step 7 conversation
  boundary; routes do not issue SQL directly.
- Cognito, Redis, AgentCore, the exact Streamlit origin, and deployment target
  remain operational configuration decisions under Step 0.

## Facts versus assumptions

### Facts

- The existing source repository already exposes driver-scoped snapshots and
  scheduling facts suitable for the deterministic feasibility evaluator.
- The existing workflow services already implement transactional delay, hold,
  proposal, expiry, and confirmation behavior.
- `BookingWorkflow.confirm_held_option` requires an idempotency identity and
  performs final capacity/ownership/expiry revalidation.
- The Terra review identified and the implementation corrected scheduler
  bypass at hold/confirm, stale option timestamps, UI confirmation replay
  identity, malformed Cognito/JWKS error handling, OpenAPI error schemas,
  chat option-to-hold progression, driver-controlled approval flags, and
  cancellation replay behavior.
- Full validation passed: 69 tests, Ruff, mypy, and editor diagnostics.

### Assumptions

- Driver-facing routes are intentionally restricted to principals in the
  Cognito `drivers` group; warehouse and operations routes remain a later
  extension.
- A local hold is exposed as `ACTIVE` with `requires_confirmation=true`; it is
  never represented as an authoritative reservation.
- The optional UI uses a manually entered Cognito access token during the pilot
  until the approved browser origin and production authentication hand-off are
  configured.

## Design, contract, state, migration, and security/operational impact

- Added Pydantic transport contracts for delay intake, shipment snapshots,
  deterministic options and rejection reasons, proposals, holds, booking
  confirmations, cancellations, chat messages, and standard error codes.
- Added `/api/v1` driver routes for shipment reads, options, delay intake,
  proposals, holds, confirm/cancel, and chat messages. The existing health,
  readiness, and API-root routes remain public.
- Added a standard error envelope containing an error code, safe message, and
  correlation ID. Request validation does not echo submitted values, and
  unexpected errors return a generic message.
- Confirmation requires the `Idempotency-Key` header. Replays return the
  original booking result through the existing workflow idempotency record.
- Hold creation carries the server-issued `options_observed_at_utc` and rejects
  stale/future option sets. The operational and booking workflows revalidate
  deterministic feasibility and reject approval-only slots before mutation.
- Pagination uses a numeric offset cursor over the repository's stable driver
  ordering; the transport contract remains cursor-based for future replacement
  with an opaque cursor if the source ordering changes.
- Added application lifespan wiring for authoritative workflow services and
  optional AgentCore/OpenRouter conversation construction. AgentCore uses the
  existing injected `bedrock-agentcore` boto3 adapter; no AWS call occurs in
  tests.
- The chat tool set now preserves the option-observed timestamp, accepts an
  explicit slot selection to create a hold, and reaches the existing explicit
  confirmation boundary.
- Added `dockit-ui` and a Streamlit client that only calls `/api/v1`; it does
  not compute feasibility or perform direct database access.
- No migration was required. The existing state schema and authoritative
  transaction boundary remain unchanged; explicit hold cancellation adds a
  transactional `RELEASED` transition and audit event through the workflow
  service.

## Changed files and symbols

| File | Symbols or change |
| --- | --- |
| `src/dockit/schemas/contracts.py` | Step 8 transport request/response models and `ErrorCode` additions. |
| `src/dockit/api/routes/operations.py` | Authenticated shipment, option, delay, proposal, hold, booking, cancellation, and chat routes. |
| `src/dockit/api/dependencies.py` | Driver-principal enforcement and service dependencies. |
| `src/dockit/api/errors.py` | Stable HTTP error envelope and exception handlers. |
| `src/dockit/api/app.py` | Router, exception, workflow, and optional conversation wiring. |
| `src/dockit/api/middleware.py` | Rate-limit responses use the standard error envelope. |
| `src/dockit/services/workflows.py` | Transactional `release_hold` operation with audit. |
| `src/dockit/services/slot_validation.py` | Deterministic slot revalidation used by hold and booking workflows. |
| `src/dockit/db/source_repository.py` | Internal shipment-facility lookup for final revalidation. |
| `src/dockit/services/booking.py` | Final scheduler-policy revalidation before appointment mutation. |
| `src/dockit/services/security.py` | Stable authentication errors for malformed JWT/JWKS failures. |
| `src/dockit/agent/orchestration.py` | Chat action/state support for selecting an option and creating a confirmation hold. |
| `src/dockit/ui/streamlit_app.py` | HTTP-only Streamlit client and pilot workspace. |
| `src/dockit/ui/__init__.py` | UI package marker. |
| `pyproject.toml` | `dockit-ui` script and UI HTTP dependency. |
| `README.md` | API/UI launch guidance. |
| `tests/test_api_operations.py` | HTTP integration, auth, OpenAPI, ownership, pagination, lifecycle, and idempotency coverage. |
| `tests/test_streamlit_client.py` | HTTP client and standard-error smoke coverage. |

## Tool/command ledger

| Tool/action | Sanitized command/action | Purpose | Result |
| --- | --- | --- | --- |
| `exec_command` | Read Step 8 plan, API shell, schemas, repositories, workflows, and scheduler adapters | Establish existing contracts and service boundaries before adding transport code. | Confirmed no operational routes existed and identified the authoritative services to call. |
| `exec_command` | Read local AWS SDK and AgentCore Harness guidance | Verify optional boto3 AgentCore client construction and invocation boundary. | Used the existing injected `bedrock-agentcore`/Harness adapter; no deployment or AWS mutation performed. |
| `apply_patch` | Added transport contracts, API routes, error handlers, lifespan wiring, UI client, service release operation, and tests | Implement Step 8 in the repository. | Applied successfully. |
| `pytest` | `.venv/bin/python -m pytest -q tests/test_api_operations.py tests/test_streamlit_client.py` | Validate focused API/UI behavior. | 7 passed. |
| `ruff` | `.venv/bin/ruff check .` | Validate lint and import rules. | Passed. |
| `mypy` | `.venv/bin/mypy src` | Validate strict source typing. | Passed for 35 source files. |
| `problems` | Checked `src/dockit`, API/UI tests, and changed service/schema files | Check editor diagnostics. | No errors found. |
| `pytest` | `.venv/bin/python -m pytest -q` | Validate the complete repository. | 69 passed; 17 third-party LangSmith deprecation warnings. |
| Review agent | Read-only GPT-5.6-Terra (Extra High) Step 8 review | Audit mutation, auth, concurrency, idempotency, OpenAPI, and UI boundaries. | Found scheduler-bypass, expiry, UI replay, JWT/JWKS, OpenAPI, chat progression, proposal-flag, and cancellation-replay issues; implementation remediated them. Synchronous event-loop work remains a Step 9 performance follow-up. |

## Tests and validation evidence

- Unauthenticated requests receive a standard `401` envelope with a
  correlation ID.
- Shipment reads are driver-scoped and cursor-paginated.
- Options are produced by the deterministic feasibility evaluator and include
  stable status, options, and rejection reasons.
- Invalid scheduler selections and option sets older than five minutes are
  rejected before a hold can be created.
- Delay intake, proposal creation, hold creation, idempotency-required
  confirmation, idempotent replay, hold cancellation, and cancellation replay
  are exercised through HTTP.
- OpenAPI includes the versioned shipment, options, confirmation, and chat
  routes and uses the standard `ErrorResponse` model for validation failures.
- Chat option selection preserves the observed option timestamp, creates an
  explicit hold, and then reaches confirmation state without exposing SQL.
- Chat safely returns `503` until a configured Redis/model boundary exists.
- The Streamlit HTTP client sends the versioned path and bearer token and
  surfaces standard API errors without adding domain decisions.

## Limitations, risks, follow-up decisions, Git state, and exact next step

- Production still requires Cognito deployment values, a supported Redis URL,
  an AgentCore/OpenRouter runtime choice, IAM/SigV4 permissions, and an
  approved Streamlit origin.
- The current app supports Redis protocol URLs for automatic conversation
  wiring; the existing Upstash REST aliases remain configuration-only until a
  dedicated REST adapter is selected and validated.
- Chat operation idempotency is deterministic per thread/hold; the external API
  uses the explicit `Idempotency-Key` header and remains the preferred mutation
  path for the UI.
- The current route and conversation implementations perform synchronous SQLite
  and model calls in the async service process. Step 9 should move these calls
  behind an explicit bounded worker/database strategy before production load.
- No deployment, AWS resource, or external message was created by this step.
- Git state: the expected Step 8 source, test, README, plan, and implementation
  record changes are uncommitted; no files were deleted and no commit was
  created by this step.

**Exact next step:** implement Step 9 operations and deployment controls:
telemetry, secrets, alerts, retention, backup/restore, migration runbooks,
feature flags, and release/rollback validation for the confirmed target.
