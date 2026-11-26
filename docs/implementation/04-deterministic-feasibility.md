# 04 — Deterministic feasibility and CP-SAT proposals

**Status:** complete  
**Completed:** 2026-11-27  
**Owner:** Codex  
**Implementation-plan step:** Step 4 in `docs/IMPLEMENTATION_PLAN.md`

## Scope and acceptance criteria

Implement a pure, deterministic feasibility evaluator that reads structured
scheduling facts through a narrow source adapter, returns ranked options and
explicit rejection reasons, and does not mutate any database. Add a bounded
CP-SAT proposal optimizer for genuine contention or a blocked direct request.
The optimizer must never write or confirm an appointment.

## Relevant chat context and confirmed decisions

- Priority order: `CRITICAL > HIGH > NORMAL > LOW`.
- Equal-priority tie-break: trucks already in the yard before in-transit
  trucks, then earlier reliable ETA.
- Low-confidence ETA: human review, not automatic allocation.
- Single slot only: the full expected unload must fit; no combined slots and no
  planned overrun.
- Facility cutoff: a candidate after last-start or beyond close is an
  operations-approval option.
- In-dock unload: the truck stays at its current dock to finish. Dock release
  uses expected finish, extended through overlapping event end; an open-ended
  event escalates.
- CP-SAT may propose moving only a strictly lower-priority
  `PENDING_CONFIRMATION` or `CONFIRMED` appointment. It never moves
  `IN_PROGRESS` work. Warehouse and affected-driver approval are mandatory
  before any proposed move.

## Facts and assumptions

| Item | Status |
| --- | --- |
| Slot/dock/appointment and event facts | Confirmed from tracked SetuHaul schema-and-seed contract. |
| Priority and tie-break order | Confirmed by the user during Step 4 clarification. |
| In-dock completion prediction | Confirmed decision: expected duration extended by overlapping event end. |
| Solver time limit | Design guard: one second, because this is bounded proposal generation rather than an authority. |
| Booking execution and approval identities | Deferred: proposals await the Step 5 workflow and Step 6 identity boundary. |

## Design and operational impact

- `SourceRepository` adds read-only facility scheduling operations. They return
  primitive source records and validate their schema only when the scheduling
  adapter requests them.
- `source_adapter.py` converts source records into immutable scheduler facts;
  the evaluator has no SQLite access.
- `feasibility.py` tests every slot against ETA, occupancy, dock type,
  refrigeration, dock weight, heavy-dock threshold, full duration, live dock
  work, events, and operating hours. It returns every rejection reason rather
  than a hidden boolean.
- `cp_sat.py` first accepts a direct deterministic option. Otherwise, its
  one-second CP-SAT model enforces one slot per shipment and one shipment per
  slot. It can return a partial batch allocation, no solution, ETA review, or
  an approval-required rescheduling proposal.
- Existing appointment changes are recommendations only. No source or DockIt
  state is mutated by these modules.

## Changed artifacts

| Artifact | Change |
| --- | --- |
| `src/dockit/db/source_repository.py` | Adds parameterized facility scheduling reads. |
| `src/dockit/scheduler/models.py` | Adds immutable scheduling facts, results, reason codes, and optimizer outcomes. |
| `src/dockit/scheduler/source_adapter.py` | Converts read-only source rows into a pure scheduling snapshot. |
| `src/dockit/scheduler/feasibility.py` | Implements deterministic filtering, explanations, and ranking. |
| `src/dockit/scheduler/cp_sat.py` | Implements bounded batch allocation and lower-priority displacement proposals. |
| `tests/test_scheduler.py` | Covers source adaptation, rules, ranking, events, approval cutoffs, and CP-SAT behavior. |
| `docs/scheduling-policy.md` | Records the implemented scheduling policy and solver boundary. |
| `docs/source-data-contract.md` | Documents the additional source read contract. |

## Tool and command ledger

| Tool/action | Purpose | Result |
| --- | --- | --- |
| Source guide/schema inspection | Map seeded edge cases, views, and constraints into scheduler facts. | Identified available slot, appointment, event, rule, queue, ETA, and facility-hours evidence. |
| Read-only SQLite inspection | Validate facility rules, active appointments, availability counts, and dock events. | Confirmed expected operational shape without mutating source data. |
| Clarifying-question workflow | Resolve ranking, confidence, cutoff, overrun, displacement, and approval policy. | User supplied the Step 4 business rules listed above. |
| Patch operations | Added source reads, pure scheduler layers, CP-SAT proposal logic, tests, and policy documentation. | Completed without source or state mutation behavior. |
| `pytest -q` | Validate the complete suite. | 43 passed; 17 third-party LangSmith deprecation warnings. |
| `ruff check .` | Validate linting/import/style rules. | Passed. |
| `mypy src` | Validate strict source typing. | Passed: no issues in 25 source files. |

## Validation evidence

- Earliest compatible deterministic options are ranked stably and incompatible,
  early, short, blocked, occupied, or event-conflicting slots show their reason.
- Low-confidence ETA is blocked from automatic allocation.
- After-hours candidates become operations-approval options.
- Active in-dock work blocks following capacity through expected completion and
  a relevant capacity-reduction event end.
- Batch CP-SAT follows priority, then yard presence, then ETA. It can propose a
  lower-priority move with the required approval flags and rejects equal/higher
  priority displacement.
- A source snapshot generated from the tracked SQL successfully converts into
  all scheduler facts.

## Limitations, risks, and next step

- The one-second bound protects service latency but a larger real-world batch
  may require a tuned limit after pilot measurements.
- The scheduler does not create holds, persist proposals, send approval
  requests, or apply any source appointment change.
- CP-SAT uses the available source snapshot; Step 5 must reread/revalidate
  source and local state inside its transaction before any promise.

**Exact next step:** implement Step 5 transactional workflows for delay intake,
proposal/hold creation, approval tracking, expiry/release, and atomic audit and
idempotency behavior—without treating any Step 4 recommendation as a booking.
