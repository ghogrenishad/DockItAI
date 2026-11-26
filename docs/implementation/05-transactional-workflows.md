# 05 — Transactional operational workflows

**Status:** complete  
**Completed:** 2026-11-27  
**Owner:** Codex  
**Implementation-plan step:** Step 5 in `docs/IMPLEMENTATION_PLAN.md`

## Scope and acceptance criteria

Implement safe delay, proposal, hold, expiry, audit, idempotency, and booking
operations. A confirmed held option must update the authoritative appointment
database atomically, preserve appointment history, and never cancel another
shipment merely to free capacity.

## Confirmed decisions

- The operational and DockIt-owned state tables share one authoritative SQLite
  database transaction.
- A typed booking tool, not direct LLM SQL, performs appointment mutation.
- Driver confirmation immediately creates a `CONFIRMED` replacement appointment.
- The same shipment's prior pending/confirmed appointment becomes historical.
- CP-SAT displacement remains proposal-only and requires the warehouse plus the
  affected driver before it can reach this booking workflow.

## Design and impact

- Unified connection mode applies DockIt migrations to the authoritative
  database while source lookup connections remain read-only.
- `OperationalWorkflow` records driver ETA updates, persists proposals, creates
  five-minute holds, and expires due holds with audit records.
- `BookingWorkflow` revalidates hold ownership, expiry, source slot state, and
  appointment occupancy inside one `BEGIN IMMEDIATE` transaction. It cancels
  only the confirming shipment's old appointment, inserts a new confirmed
  appointment, releases the hold, records the idempotent result, and audits it.
- Migration 3 stores idempotent booking results; migration 4 stores proposals.

## Changed artifacts

| Artifact | Change |
| --- | --- |
| `src/dockit/services/booking.py` | Atomic confirmed appointment replacement and replay handling. |
| `src/dockit/services/workflows.py` | Delay, proposal, hold, and expiry workflow primitives. |
| `src/dockit/db/connection.py`, `src/dockit/core/config.py` | Unified authoritative connection mode. |
| `migrations/3_booking_idempotency_results.sql`, `migrations/4_booking_proposals.sql` | Booking replay and proposal persistence. |
| `tests/test_booking.py` | Booking, stale capacity, delay, hold, proposal, expiry, and audit coverage. |

## Validation evidence

- A confirmation cancels the prior appointment for the same shipment, creates
  one new current confirmed appointment, releases its hold, and audits the
  operation.
- Retrying the same idempotency identity returns the original appointment with
  no duplicate current appointment.
- A newly occupied slot is rejected during final revalidation.
- Delay reports, proposal creation, holds, and expiry write their audit events
  in the authoritative transaction.
- `pytest -q`: 47 passed; `ruff check .` and `mypy src` passed.

## Limitations and next step

- Approval identity is still an opaque subject until Step 6 Cognito integration.
- Workflows are service primitives only; Step 8 supplies public HTTP routes.
- Step 6 should add authorization and protections before any external caller
  can invoke the booking tool.

**Exact next step:** implement Step 6 identity, authorization, rate limits, and
production protections around these authoritative operations.
