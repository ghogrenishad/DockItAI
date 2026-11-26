# 02 — Persistence and source-data boundary

**Status:** complete  
**Completed:** 2026-11-26  
**Owner:** Codex  
**Implementation-plan step:** Step 2 in `docs/IMPLEMENTATION_PLAN.md`

## Scope and acceptance criteria

Implement a safe persistence boundary around the supplied SetuHaul operational
snapshot. The source database must be read-only and never migrated. DockIt must
keep independent writable state for local holds, idempotency records, audit
events, conversation references, and migration tracking. Migrations must be
ordered, checksummed, additive, transaction-controlled, and recoverable after
an unapplied failure.

## Relevant context and decisions

- The user asked to proceed with Step 2 as a learning-oriented implementation
  and requires each step to leave a durable record of decisions, design, chat
  context, tool calls, validation, limitations, and the next step. Their model
  preference is Terra (High) for implementation, Terra (Extra High) for its
  review, and GPT-6-Luna for documentation.
- The tracked source contract is defined by the database guide, ER diagram,
  dictionary, and `data/setuhaul_schema_and_seed.sql`. A local
  `data/dockit.db` snapshot may be present for development, but it is ignored
  and is not a tracked or deployable artifact.
- The guide's historical filename is `setuhaul_freight_operations.db`; the
  delivered SQLite file is `dockit.db`. Configuration now uses the delivered
  filename while retaining an environment override for production.
- SetuHaul remains the source-data owner. DockIt has no source mutation or
  source-migration authority.
- Step 0 has not resolved external booking authority. Therefore a local
  `slot_holds` row is explicitly an unconfirmed DockIt state record, never a
  source appointment or a warehouse confirmation.

## Facts and assumptions

| Item | Status |
| --- | --- |
| Supplied source snapshot | Verified: 18 tables, four operational views, 21 shipments, 106 slots, 20 appointments. |
| Source query scope | Confirmed implementation: driver-scoped summaries and owned-shipment snapshots from `v_inbound_operational_state`. |
| SQLite source writes | Verified: `mode=ro` and `query_only` deny DML and DDL. |
| State/source separation | Confirmed design: separate connections and databases; no cross-database foreign keys. |
| Hold lifecycle and confirmation authority | Intentionally deferred to Steps 3 and 5. |

## Design and operational impact

- `connect_source_database` produces a distinct read-only connection type and
  returns SQLite mapping rows. `SourceRepository` validates its required view
  and columns before exposing parameterized driver-scoped reads.
- `connect_state_database` enables foreign keys, WAL, and a busy timeout only
  for DockIt state. `BEGIN IMMEDIATE` remains the transaction primitive for
  allocation paths.
- `MigrationRunner` only accepts the explicit state connection type. It applies
  migrations in numeric order, records SHA-256 checksums, rejects checksum or
  transaction-control drift, and rolls back each failed migration, including
  its tracking record.
- `1_initial_state.sql` creates `conversation_references`, `slot_holds`,
  `idempotency_records`, and `audit_events`. The state repository does not open
  transactions so Step 5 can make hold, idempotency, and audit writes atomic in
  one service-owned transaction.
- Application startup validates the source contract, opens the state database,
  applies pending state migrations, stores the repositories on application
  state, and closes both connections at shutdown.
- Canonicalized source/state paths are checked so the two databases cannot
  resolve to the same file. This guards against misconfiguration making a
  writable state connection point at the source database.
- Tests create isolated source databases under `tmp_path` by executing the
  tracked schema-and-seed SQL; they do not depend on the ignored local snapshot.
- The complete source contract and migration recovery procedure are in
  `docs/source-data-contract.md`.

## Changed artifacts

| Artifact | Change |
| --- | --- |
| `src/dockit/db/connection.py` | Adds distinct source/state connection types, view metadata, and read-only source mapping rows. |
| `src/dockit/db/source_repository.py` | Adds checked, parameterized driver-scoped source reads. |
| `src/dockit/db/state_repository.py` | Adds persistence primitives for the initial DockIt state schema. |
| `migrations/1_initial_state.sql` | Adds the first additive state migration and local state indexes/constraints. |
| `src/dockit/api/app.py` | Initializes persistence resources at application lifecycle boundaries. |
| `docs/source-data-contract.md` | Documents source ownership, stable reads, source/state separation, and migration recovery. |
| `tests/test_persistence.py`, `tests/test_source_repository.py`, `tests/test_system.py` | Adds source contract, source immutability, state migration/recovery, foreign-key, writer-contention, and lifecycle evidence. |

## Tool and command ledger

| Tool/action | Purpose | Result |
| --- | --- | --- |
| Repository/data inspection | Read the implementation plan, source guide, dictionary, schema seed, existing persistence code, and tests. | Confirmed Step 2 scope and supplied snapshot contract. |
| Read-only SQLite inspection | Checked local development snapshot against the tracked source contract. | Confirmed the expected relation/view shape; individual operational identifiers are intentionally omitted from this durable record. |
| Patch operations | Added state migration/repositories, source contract documentation, lifecycle initialization, config/default updates, and focused tests. | Completed without touching the source SQLite database. |
| Documentation update (`apply_patch`) | Reconciled this record, the plan, README, migration guide, and source-data contract with the accepted final implementation. | Corrected stale test count and local-snapshot assumptions; documented runtime provisioning and the final review result. |
| `pytest -q` | Validate the suite after review remediation. | 26 passed; 17 third-party LangSmith deprecation warnings only. |
| `ruff check .` | Validate linting/import/style rules. | Passed. |
| `mypy src` | Validate strict source typing. | Passed: no issues in 19 source files. |
| GPT-5.6-Terra (Extra High) implementation review | Review Step 2 boundaries and remediation. | Accepted after remediation; final validation is the 26-test run above. |
| Documentation consistency scan (`rg`) | Check for the stale 23-test result and outdated source-database assumptions. | Current plan/record state reflects 26 passing tests and the ignored-snapshot/runtime-provisioning boundary. |

## Validation evidence

- Fresh and upgrade migration ordering, checksum drift, transaction-control
  rejection, and backfilled-version rejection are covered.
- A failed unapplied migration rolls back fully, can be corrected, and then
  reapplies successfully.
- Test fixtures built from the tracked seed SQL expose all four documented
  views; driver-scoped reads return ordered mapping rows and deny cross-driver
  access.
- Source read-only behavior and query-only mode are verified using disposable
  fixture databases rather than relying on a tracked database binary.
- State migration creation and repository writes are covered, including the
  active-hold uniqueness constraint, state foreign keys, and immediate
  transaction writer contention.
- Application endpoint tests confirm lifecycle initialization works with an
  isolated temporary state database.

## Limitations, risks, and next step

- Holds are local, unconfirmed records until the Step 3 policy and Step 5
  booking protocol define TTL, state transitions, source revalidation, and
  external compensation.
- Only the safe, driver-scoped source reads are stable in this step. Facility-
  wide feasibility reads belong to Step 4, after the Pydantic contracts and
  policy rules are explicit.
- Because `data/*.db` is ignored, runtime environments must provision a
  schema-compatible, read-only SetuHaul database or set
  `DOCKIT_SOURCE_DATABASE_PATH` to its location. They must also provide a
  separately backed-up writable state path through
  `DOCKIT_STATE_DATABASE_PATH`.

**Exact next step:** implement Step 3 typed contracts, UTC/facility-time policy,
hold/idempotency/audit semantics, and the operational state machine without
granting any source-database mutation authority.
