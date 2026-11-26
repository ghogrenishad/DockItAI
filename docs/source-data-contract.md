# SetuHaul source-data contract

## Boundary

`data/setuhaul_schema_and_seed.sql` is the tracked source schema-and-seed
contract. A local `data/dockit.db` may be materialized for development, but
database files under `data/` are ignored and are not shipped as application
inputs. The database guide's historical filename is
`setuhaul_freight_operations.db`; runtime deployments must provision the
compatible database and configure `DOCKIT_SOURCE_DATABASE_PATH` when it is not
at the default path.

SetuHaul owns the operational tables. DockIt opens ordinary lookup connections
using SQLite `mode=ro` and enables `query_only`. The confirmed booking workflow
uses one separate unified authoritative connection to the same database: it may
apply DockIt-owned migrations and atomically update `appointments` after a
driver confirms a held option. The LLM never receives direct SQL access.

At startup, the lifecycle opens a source read-only connection and a unified
authoritative connection, validates the required source relations and columns,
and applies pending DockIt-owned migrations. In unified mode, appointments,
holds, idempotency records, and audits share one transaction.
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

The repository exposes parameterized reads only; no generic SQL interface is
available to application layers. Driver-scoped reads resolve a conversation to
an existing source shipment. Step 4 adds facility-wide reads for the pure
scheduler adapter.

| Operation | Source relation | Stable ordering / purpose |
| --- | --- | --- |
| `list_driver_shipment_summaries(driver_id)` | `v_inbound_operational_state` | `effective_eta_ts`, `shipment_id`; lists a driver's shipment, facility, priority, appointment, slot, and current queue summary. |
| `get_driver_shipment_snapshot(driver_id, shipment_id)` | `v_inbound_operational_state` | The same fields for one shipment only when it belongs to the supplied driver. |
| `get_scheduling_facility(facility_id)` | `facilities` | Active facility timezone, hours, and default duration. |
| `list_scheduling_shipments(facility_id)` | `shipments`, `vehicles`, `v_latest_eta`, `facility_checkins` | Active facility demand with ETA confidence, equipment, weight, and yard state. |
| `list_scheduling_slots(facility_id)` | `appointment_slots`, `docks`, `appointments` | Every slot with dock capability and active occupancy. |
| `list_scheduling_appointments(facility_id)` | `appointments`, `shipments`, `appointment_slots`, `v_latest_eta` | Current active appointments eligible to remain fixed or become an approval-only proposal. |
| `list_scheduling_rules(facility_id)` | `facility_rules` | Active facility policy values. |
| `list_scheduling_dock_events(facility_id)` | `dock_status_events`, `docks` | Event windows used to block conflicting dock capacity. |

The repository verifies the driver read contract at startup and validates the
additional source relations lazily when a scheduling read is requested. Input
identifiers are values, not SQL fragments. It returns primitive source values
only; it does not silently apply scheduling, capacity, authorization, or
booking policy.

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

The booking workflow must still reread capacity before it makes a promise and
uses `BEGIN IMMEDIATE` for the authoritative appointment, hold, idempotency,
and audit transaction. Read-only lookup paths remain separated from mutation.

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
