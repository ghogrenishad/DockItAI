# 03 — Contracts, policy, and state machine

**Status:** complete  
**Completed:** 2026-11-26  
**Owner:** Codex  
**Implementation-plan step:** Step 3 in `docs/IMPLEMENTATION_PLAN.md`

## Scope and acceptance criteria

Create the Pydantic contracts shared by future API, services, scheduling, and
agent layers. Define time, local-hold, idempotency, ownership, revalidation,
audit, error, and pagination policy. Guard valid exception-state transitions.
Do not implement booking, source writes, external integrations, or operational
workflows.

## Relevant chat context and decisions

- The user asked to continue with the next implementation step after Step 2.
- The user confirmed that a hold starts when chat presents options, prompts once
  after 2.5 minutes without a response, and releases after five minutes.
- The product owner has not selected an authoritative booking system, identity
  claim shape, or reservation protocol. The local-only policy therefore accepts
  a confirmation request but does not treat it as a booking or permit a local
  `CONFIRMED` reservation state.

## Facts and assumptions

| Item | Status |
| --- | --- |
| Contract implementation | Confirmed: Pydantic v2 models are available and strict typing validates 21 source files. |
| Time storage | Confirmed roadmap decision: UTC storage; facility display is explicit. |
| Hold and reminder timing | Confirmed: start at option delivery; one reminder at 2.5 minutes; release at five minutes. |
| Actor ownership | Provisional assumption: exact opaque subject equality until Cognito claims arrive in Step 6. |
| Idempotency scope | Provisional policy: `(actor_subject, operation, key)` with a request fingerprint. |
| Confirmation | Confirmed safety boundary: request only until Step 5 has an authoritative booking protocol. |

## Design and operational impact

- `contracts.py` centralizes constrained identifiers, UTC timestamp
  normalization, facility-local presentation, appointment, delay-report,
  candidate-option, local-hold, confirmation, idempotency, audit, error, and
  pagination models.
- Contract models reject unknown fields. Delay reports require either a
  declared ETA or delay duration; options require a positive time window; and
  hold finalization timestamps must match their status.
- Audit events are frozen Pydantic models. Future persistence converts an event
  to its serialized payload before appending it; no update contract exists.
- `policy.py` defines the fixed 2.5-minute reminder and five-minute expiry from
  the option-delivery timestamp, non-disableable source/local-hold/
  authoritative-booking revalidation requirements, ownership checking, and the
  guarded local lifecycle:
  `REPORTED → TRIAGED → ALTERNATIVES_FOUND → PROPOSAL_PENDING_CONFIRMATION → HELD`.
- A held local request may expire or cancel. Cancellation and rejection are
  terminal. `CONFIRMED` is intentionally not a local transition, preventing
  callers from mistaking a DockIt hold for a SetuHaul reservation.
- The full policy and the decisions still requiring product-owner confirmation
  are described in `docs/operational-policy.md`.

## Changed artifacts

| Artifact | Change |
| --- | --- |
| `src/dockit/schemas/contracts.py` | Adds strict shared Pydantic contracts, ID constraints, time normalization, models, and schema examples. |
| `src/dockit/schemas/policy.py` | Adds local hold/revalidation policy, ownership checks, and state-transition guards. |
| `migrations/2_hold_option_delivery_timing.sql` | Adds option-delivery and reminder timestamps to local holds without modifying existing migrations. |
| `src/dockit/db/state_repository.py` | Persists option delivery and records exactly one active-hold reminder. |
| `tests/test_contracts.py`, `tests/test_persistence.py` | Adds serialization, timing, reminder, persistence, retry/ownership, and transition evidence. |
| `docs/operational-policy.md` | Documents policy assumptions, revalidation rules, and the local state machine. |
| `src/dockit/api/app.py` | Corrects the lifespan annotation to satisfy the project’s Ruff rule. |
| `docs/IMPLEMENTATION_PLAN.md`, `docs/implementation/README.md` | Mark Step 3 complete and record its policy boundary. |

## Tool and command ledger

| Tool/action | Purpose | Result |
| --- | --- | --- |
| Plan, persistence, and source inspection | Verify Step 3 scope and actual state-table constraints before adding contracts. | Confirmed local holds are unconfirmed and no booking integration is authorized. |
| Baseline quality commands | Check the repository before contract changes. | 26 tests passed; Ruff exposed one existing lifespan generic-annotation violation; mypy passed. |
| Patch operations | Added contracts, policy guards, focused tests, policy documentation, and the minimal Ruff annotation correction. | Completed without source database or migration changes. |
| `pytest -q` | Validate the complete suite. | 35 passed; 17 third-party LangSmith deprecation warnings. |
| `ruff check .` | Validate linting/import/style rules. | Passed. |
| `mypy src` | Validate strict source typing. | Passed: no issues in 21 source files. |

## Validation evidence

- Offset-bearing timestamps normalize to UTC; naive timestamps fail validation;
  facility-local display uses a valid IANA timezone.
- Delay, candidate-option, appointment, pagination, error, and audit contracts
  serialize correctly and publish Pydantic schema examples.
- Local holds validate finalization and expiry boundaries; five-minute default
  expiry is deterministic.
- Confirmation retries bind to the same actor/idempotency scope, and ownership
  mismatches fail before any future side effect.
- Valid lifecycle progression is accepted while unsupported jumps, terminal
  transitions, and an unsupported local confirmation state are rejected.
- Revalidation requirements cannot be disabled through contract input.

## Limitations, risks, and next step

- The option-delivery, reminder, and release timing are confirmed. Identity
  equality and the detailed lifecycle remain provisional until Cognito and the
  booking protocol are selected.
- No public route uses these models yet; Step 8 will expose them after the
  service workflow and authorization boundaries exist.
- There is no booked/confirmed reservation state until the system of record,
  compensation behavior, and transaction protocol are approved.

**Exact next step:** implement Step 4 deterministic feasibility as pure
retrieval/evaluation/ranking over the source contract, but only after the
facility constraint and ranking decisions are confirmed or documented as
accepted assumptions.
