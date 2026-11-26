# DockIt AI implementation plan

**Status:** Roadmap reviewed; scope decisions still pending before implementation  
**Prepared:** 2026-11-26  
**Planning model:** GPT-5.6-Terra (Extra High)

## Evidence and planning limit

DockIt is intended to help SetuHaul freight drivers report delays, find feasible dock alternatives, and safely reschedule appointments. The repository defines the intended boundaries: FastAPI (api), LangChain and Redis (agent), deterministic/CP-SAT scheduling (scheduler), SQLite and migrations (db), Pydantic contracts (schemas), and business workflows (services).

The workspace is a scaffold. Only core/config.py contains application logic; there are no API endpoints, repositories, migrations, scheduler rules, agent flows, tests, or tracked seed database.

The delivery roadmap was reviewed in Notion on 2026-11-26. It confirms the
product boundary, MVP UI, and key technology choices below. It does not define
the remaining operational and integration details needed to safely implement
booking, so Step 0 remains an in-progress decision gate. The user authorized
the safe, framework-only Step 1 foundation before that gate closes; booking,
source-data, scheduling, and external-integration implementation remain gated.

## Governing decisions

1. The service/domain layer is authoritative. The roadmap expressly prohibits
   the LLM from deciding capacity, dock compatibility, business priority, or
   final booking. It may extract intent, request missing facts, explain
   options, and call typed tools.
2. Read the SetuHaul source database only until its ownership and mutability are confirmed. Store DockIt holds, idempotency data, audit events, and migration metadata in a separate writable state database.
3. A confirmation is safe only when its service transaction revalidates capacity, guards against races, writes the result atomically, and appends an audit event.
4. Start with transparent deterministic feasibility. Use bounded CP-SAT only for confirmed contention or batch-optimization requirements.
5. Store instants in UTC and make facility-local presentation explicit.
6. Streamlit is the selected MVP driver UI for chat, option comparison,
   confirmation, and escalation status. It remains an HTTP client and contains
   no copied domain policy.
7. Cognito owns human identity, roles, and JWT issuance. Redis holds only
   TTL-bound conversation state keyed by driver and thread; it is never the
   capacity authority.
8. Each completed implementation step gets an immutable, sanitized completion record in docs/implementation.

## Confirmed roadmap traceability

| Roadmap statement | Plan treatment |
| --- | --- |
| Authenticated drivers report delays; the system resolves the correct shipment and asks only for missing facts. | Steps 3, 5, 6, and 7 define the identity-bound delay/reporting contracts, workflow, authorization, and clarification behaviour. |
| Return feasible, explainable options and avoid conflicting capacity promises. | Steps 4 and 5 implement deterministic feasibility, transparent rejection reasons, transactional revalidation, idempotency, and concurrency evidence. |
| Escalate no-slot and risky cases safely. | Steps 3, 5, 7, and 8 add explicit escalation state/contracts, service workflow, conversational hand-off, and UI/API status. Exact escalation owner remains open. |
| Streamlit provides driver chat, option comparison, confirmation, and escalation status. | Step 8 now includes Streamlit as an MVP deliverable, after stable HTTP contracts exist. |
| Cognito provides human identity, roles, and JWTs. | Step 6 verifies Cognito JWTs and enforces service-boundary authorization. |
| AWS IAM and boto3 SigV4/HMAC provide service access and AgentCore invocation. | Steps 6, 7, and 9 use least-privilege IAM and SigV4-authenticated AgentCore invocation; exact runtime/deployment configuration remains open. |
| LangChain handles intent extraction, clarification, tool selection, and response composition. | Step 7 implements constrained typed-tool orchestration, not operational decision making. |
| Upstash Redis holds TTL-bound ConversationState by driver and thread and is never authoritative capacity. | Step 7 binds short-lived state to the authenticated principal and defines degraded behaviour; Step 5 remains the capacity authority. |
| Pydantic v2 contracts cross API, tool, scheduler, and persistence boundaries. | Step 3 creates these contracts before adapters. |
| OR-Tools CP-SAT makes facility-wide allocation proposals over structured facts. | Step 4 implements a bounded CP-SAT adapter behind a deterministic feasibility contract. |

## Explicitly out of scope

The roadmap excludes live GPS, national network optimisation, carrier
selection, freight pricing, autonomous safety decisions, customs/legal
workflows, and commercial penalty approval. No implementation step may add
these capabilities without a new confirmed roadmap decision.

## Step 0 — reconcile the delivery roadmap

**Goal:** establish confirmed scope and acceptance criteria before production code.

**Collect and decide:**

- MVP journeys, milestones, delivery dates, pilot facilities/geography, success measures, and launch/rollback criteria.
- Roles, tenant model, permissions, approvals/escalations, and operational owner of every decision.
- Appointment source of truth and permitted mutation authority; real reservation versus DockIt-only hold; compensation for external failures.
- Seed database location/schema/owner, external integrations, data classification, retention, audit, and residency requirements.
- Required feasibility constraints/objectives: capacity, hours/cutoffs, intervals, ETA/buffers, equipment/commodity, driver restrictions, and promises.
- AgentCore runtime model/configuration and failure policy; Cognito claims; Redis degraded behaviour; deployment target; and pilot UI acceptance criteria.

**Completed in this plan:** roadmap-to-step traceability, product goals,
out-of-scope boundaries, Streamlit/Cognito/LangChain/Redis/Pydantic/CP-SAT
choices, and the prohibition on LLM operational decisions.

**Remaining deliverables:** confirmed acceptance tests and written decisions
for the appointment source of truth, booking mutation authority, seed-data
contract, hold/expiry policy, facility constraints/objectives, escalation
owner, PII/retention policy, AgentCore model/runtime configuration, deployment
target, and pilot measures.

**Exit:** the product owner confirms the remaining decisions above—or
explicitly accepts documented assumptions. Do not write booking or integration
code before this gate closes.

## Step 1 — foundation and service shell

**Implement:**

- Typed environment settings for source/state database paths, Redis, Cognito, AWS/Bedrock, logging, limits, and production fail-fast validation.
- FastAPI app factory, lifespan/dependencies, versioned root, health/readiness, correlation ID, structured redacted logs, deterministic test configuration.
- Quality commands and CI/lockfile decision; verify Python 3.14 compatibility locally and in the selected runtime.
- Initial pytest, httpx, Ruff, and mypy smoke coverage.

**Validate:** service boot, health/readiness, invalid configuration, correlation propagation, lint/type/test suite.

**Exit:** a minimal service runs predictably and quality failures are actionable.

## Step 2 — persistence and source-data boundary

**Implement:**

- Inspect a safe copy of the seed schema read-only; document a stable source-query contract.
- Read-only source repositories; separate writable state connection for holds, idempotency keys, audit events, conversation references, and migration metadata.
- Ordered, checksum-recorded, transactional, additive migrations plus a runner. Never migrate the seed database.
- SQLite rules: foreign keys, busy timeout, WAL only for state, and BEGIN IMMEDIATE for allocation paths.
- Non-production fixtures and recovery/rollback behaviour.

**Validate:** fresh/upgrade migrations, checksum/partial failure handling, foreign keys, concurrent writes, source DB unchanged.

**Exit:** DockIt state is reproducible and isolated from operating data.

## Step 3 — contracts, policy, and state machine

**Implement:**

- Pydantic models for typed IDs, appointment data, delay reports, options, holds, confirmations, audit events, errors, and pagination.
- UTC/facility-time policy, idempotency key scope, hold TTL, actor ownership, revalidation rules, immutable audit shape, and error/OpenAPI compatibility policy.
- A roadmap-confirmed exception/reservation state machine. A possible example is reported → triaged → alternatives found → proposal pending confirmation → held → confirmed, expired, cancelled, or rejected; it is not yet a requirement.

**Validate:** contract serialization/OpenAPI examples, valid/invalid transition tests, expiration/retry tests, time-zone boundaries.

**Exit:** layers share explicit policy contracts and cannot bypass valid transitions.

## Step 4 — deterministic feasibility

**Implement:**

- Pure retrieval/evaluation/ranking interfaces for appointments and candidate slots.
- Only confirmed constraints: capacity, operating hours/cutoffs, intervals, ETA/buffers, equipment/commodity, driver restrictions, and existing promises.
- Candidate explanations, constraint-failure reasons, objective weights, deterministic tie-breaks, and a stable no-solution result.
- A bounded CP-SAT implementation behind the same interface where actual contention/batch optimization needs it.

**Validate:** realistic fixtures, constraint-boundary and invariant tests, deterministic ordering, no-solution and time-limit behaviour.

**Exit:** DockIt can explain feasible and infeasible options with no LLM.

## Step 5 — transactional operational workflows

**Implement:**

- Services for delay reporting, appointment lookup, option search, hold create/revalidate/release, proposal, explicit confirm, cancel, and expiry.
- Atomic capacity guard, idempotent command processing, and same-transaction audit writes.
- The Step-0-selected protocol for an authoritative external booking system; a local hold must not masquerade as a real reservation.

**Validate:** duplicate/retry requests, stale candidate, expiry/release, cancellation, two concurrent confirmations, complete audit data, no conflicting promise.

**Exit:** the operational core is safe without chat or public UI.

## Step 6 — identity and protections

**Implement:**

- Cognito JWT verification with cached JWKS and strict issuer, audience/client, algorithm, expiry, tenant, and role validation.
- Principal model and service-boundary authorization.
- Least-privilege IAM roles and the boto3 SigV4/HMAC invocation boundary used
  for AgentCore and other AWS service access.
- Request limits, narrow CORS, security headers, log redaction, rate limits, a defined Redis-degraded mode, and production-disabled test auth.

**Validate:** valid/expired/wrong-issuer/wrong-audience/wrong-tenant/wrong-role/missing-token cases; rate-limit and redaction tests.

**Exit:** only authorized principals access their permitted data and mutations.

## Step 7 — constrained conversational orchestration

**Implement:**

- LangChain orchestration over typed services only; no database/SQL tool access.
- Structured intent/entity output, validation/clarification, Redis session TTL with tenant/principal binding and a safe fallback.
- Prompt/tool policy against fabricated availability, authorization bypass, prompt injection, and unconfirmed booking.
- Human-visible proposal summaries, explicit confirmation, and mandatory server revalidation.
- Invoke the roadmap-selected AgentCore service with the Step-6 IAM/SigV4
  boundary. The remaining Step-0 decision is the model, runtime configuration,
  and failure/timeout policy. GPT-5.6-Terra was the planning model, not a
  runtime-model selection.

**Validate:** tool-mocked multi-turn path, ambiguity, injection, unavailable inference/Redis, authorization, confirmation, and schema-error tests.

**Exit:** conversation aids operation but cannot dictate it.

## Step 8 — HTTP API and Streamlit pilot UI

**Implement:**

- Versioned endpoints for delay intake, appointment/option reads, proposal/hold, confirm/cancel, chat/session, health, and readiness.
- Standard errors, idempotency headers, pagination, correlation IDs, and OpenAPI examples.
- Deliver the roadmap-selected Streamlit driver experience for chat, option
  comparison, explicit confirmation, and escalation status. It remains an HTTP
  client with no copied domain policy.

**Validate:** httpx integration tests, OpenAPI review, auth checks, idempotent mutations, and UI smoke tests if included.

**Exit:** documented API consumers can complete the confirmed journeys.

## Step 9 — operations and deployment

**Implement:**

- Request/workflow/solver/inference/hold/error telemetry correlated to audit events.
- Secret configuration, alert thresholds, retention, backup/restore, migration rollout/rollback, runbooks, and feature flags.
- Deployment artifacts/infrastructure for the confirmed target only.

**Validate:** staging smoke test, migration rehearsal, restore drill, threat review, alert test, release/rollback walkthrough.

**Exit:** the pilot is diagnosable, recoverable, and releasable.

## Step 10 — scenario, performance, and pilot acceptance

**Implement and run:** replay representative non-production cases: delay-to-confirmation, no capacity, conflicting drivers, cancel/retry, approvals, source/booking outage, LLM/Redis outage, auth failure, and migration failure. Derive load/concurrency/solver benchmarks from the pilot workload and run UAT.

**Exit:** an evidence report maps results to every roadmap acceptance criterion and records accepted residual risks.

## Dependency sequence

0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10

Feasibility precedes booking side effects. Identity/protections precede externally reachable conversational/API work.

## Open decisions

| Decision | Required resolution |
| --- | --- |
| Roadmap scope and dates | Authenticate the Notion page or provide an export/paste. |
| Source database ownership/schema | Inspect a safe copy and confirm owner/contract. |
| Hold versus external reservation | Define authority, TTL, approval, and compensation behaviour. |
| Scheduling rules/objectives | Confirm facility/business constraints and rank priorities. |
| AgentCore/runtime/deployment | Confirm the runtime model, AgentCore configuration, failure policy, and target environment. |
| Identity/data policy | Confirm claims, tenancy, PII, retention, and residency. |

## Provenance

- The user requested a detailed roadmap-based plan and durable implementation records.
- Reviewed evidence: pyproject.toml, README.md, .env.example, migrations/README.md, and all application-source files.
- The Terra planning pass was run at Extra High reasoning effort.
- The authenticated Notion roadmap was reviewed on 2026-11-26. Its confirmed
  requirements are mapped in the traceability table; absent details remain
  explicitly open.

## Next action

Provide authenticated Notion access or paste/export the roadmap. Then complete Step 0 by adding a roadmap-to-step traceability table here before implementation begins.
