# SetuHaul source-data contract

## Boundary

`data/setuhaul_schema_and_seed.sql` is the tracked source schema-and-seed
contract. A local `data/dockit.db` may be materialized for development, but
database files under `data/` are ignored and are not shipped as application
inputs. The database guide's historical filename is
`setuhaul_freight_operations.db`; runtime deployments must provision the
compatible database and configure `DOCKIT_SOURCE_DATABASE_PATH` when it is not
at the default path.

SetuHaul owns this database. DockIt opens it using SQLite `mode=ro` and enables
`query_only`; it never runs migrations, DDL, or writes against it. The source
connection is only for reading operational state. DockIt keeps its own mutable
state in `DOCKIT_STATE_DATABASE_PATH`.

At startup, the lifecycle opens the source read-only, opens the distinct
DockIt-owned state database, validates the required source relations and
columns, and applies pending state migrations. Canonical path checks prevent
source and state from resolving to the same database file.
It intentionally does not use snapshot row counts as a production compatibility
check: operational rows are expected to change while the contract remains
stable.

## Source snapshot facts

- Snapshot timestamp: 2026-08-04 in `Asia/Kolkata`.
- Relations: 18 tables and four views.
- Operational views: `v_latest_eta`, `v_slot_availability`,
  `v_inbound_operational_state`, and `v_current_facility_queue`.
- Timestamps are ISO-8601 text carrying a `+05:30` offset in the supplied
  snapshot. The source repository preserves these strings exactly; UTC/domain
  conversion is a Step 3 contract concern.
- The source enforces a single active appointment per slot and a single current
  active appointment per shipment. These constraints do not cover local
  DockIt holds, so a later confirmation must always reread and revalidate the
  source data in its state transaction.

## Stable read operations

Step 2 intentionally provides only the driver-scoped reads needed to resolve a
conversation to an existing source shipment. They are parameterized and no
generic SQL interface is exposed to application layers.

| Operation | Source relation | Stable ordering / purpose |
| --- | --- | --- |
| `list_driver_shipment_summaries(driver_id)` | `v_inbound_operational_state` | `effective_eta_ts`, `shipment_id`; lists a driver's shipment, facility, priority, appointment, slot, and current queue summary. |
| `get_driver_shipment_snapshot(driver_id, shipment_id)` | `v_inbound_operational_state` | The same fields for one shipment only when it belongs to the supplied driver. |

The repository verifies the view has the required identifiers, source status,
ETA, current appointment/slot, and queue columns. Input identifiers are values,
not SQL fragments. It returns primitive source values only; it does not silently
apply scheduling, capacity, authorization, or booking policy. Step 4 adds the
separate facility-wide scheduling reads once its policy contracts are defined.

## DockIt-owned state

Migration `1_initial_state.sql` creates these independent state tables:

- `conversation_references` — mappings to source thread/shipment identifiers;
  source messages and contacts are not copied.
- `slot_holds` — local, unconfirmed holds. A partial unique index prevents two
  active DockIt holds for the same source slot. It records the option-delivery
  time and whether the one chat reminder was sent; these are never source data.
- `idempotency_records` — actor- and operation-scoped request identities.
- `audit_events` — immutable application audit entries, optionally tied to a
  local hold.
- `_dockit_schema_migrations` — runner-owned migration version/checksum data.

There are no foreign keys from the state database to the source database:
SQLite cannot enforce referential integrity across the deliberately separate
connections. A workflow must treat source IDs as opaque references, reread the
source before it makes a promise, and persist its local changes with
`BEGIN IMMEDIATE`.

## Migration recovery

Migrations are numerically ordered, additive, SHA-256 checksummed, and each
executes in one transaction. Applied-file drift and SQL transaction-control
statements are rejected. A failed unapplied migration leaves none of its
statements or tracking row behind; correct that unapplied file and retry. Once
a migration is recorded, it is immutable: ship a later corrective migration
instead of editing or deleting it. Operational rollback is a database-backup
restore followed by a replay of the approved migration sequence; DockIt does not
attempt an automatic destructive downgrade.

Tests create disposable source databases under `tmp_path` by executing the
tracked schema-and-seed SQL, so they do not depend on the ignored local
`data/dockit.db` snapshot. At runtime, provision the SetuHaul source database
and configure `DOCKIT_SOURCE_DATABASE_PATH` as needed; keep the writable
DockIt-owned database separately configured through
`DOCKIT_STATE_DATABASE_PATH`.
