# Application migrations

The supplied SetuHaul seed database remains unchanged. This directory will contain
ordered, additive SQL migrations for DockIt-owned state such as slot holds,
idempotency keys, and audit events.

Migrations are applied by the application in a later milestone; do not manually
apply them to the supplied source database.
