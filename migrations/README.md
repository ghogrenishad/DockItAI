# Application migrations

This directory contains ordered, additive DockIt-owned migrations. In unified
authoritative mode, those tables share the appointment database transaction;
the operational schema remains owned by SetuHaul.

Use the filename format `N_descriptive_name.sql`, where `N` is a unique positive
integer (for example, `1_initial_state.sql`). SQL is applied in numeric order,
inside a transaction, and recorded with a SHA-256 checksum. Applied migration
checksum drift is rejected. Once a migration has been applied, it is immutable:
add a later corrective migration instead of editing or deleting the original.

The migration runner owns transaction boundaries. Do not include `BEGIN`,
`COMMIT`, `ROLLBACK`, `SAVEPOINT`, or `RELEASE` statements in a migration.

`1_initial_state.sql` creates DockIt-owned persistence primitives: local
holds, idempotency records, audit events, and references to source conversations.
It does not copy or modify SetuHaul appointments, slots, messages, or contacts.
`2_hold_option_delivery_timing.sql` records when options were shown and whether
the one 2.5-minute chat reminder was sent, while preserving existing holds.
The detailed source-query contract is documented in
[`docs/source-data-contract.md`](../docs/source-data-contract.md).
