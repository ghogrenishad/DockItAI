CREATE TABLE conversation_references (
    conversation_reference_id TEXT PRIMARY KEY,
    principal_subject TEXT NOT NULL,
    source_thread_id TEXT,
    source_shipment_id TEXT,
    created_at_utc TEXT NOT NULL,
    last_seen_at_utc TEXT NOT NULL,
    CHECK (last_seen_at_utc >= created_at_utc)
);

CREATE UNIQUE INDEX ux_conversation_references_source_thread
ON conversation_references(source_thread_id)
WHERE source_thread_id IS NOT NULL;

CREATE TABLE slot_holds (
    hold_id TEXT PRIMARY KEY,
    source_shipment_id TEXT NOT NULL,
    source_slot_id TEXT NOT NULL,
    actor_subject TEXT NOT NULL,
    conversation_reference_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'RELEASED', 'EXPIRED')),
    created_at_utc TEXT NOT NULL,
    expires_at_utc TEXT NOT NULL,
    finalized_at_utc TEXT,
    FOREIGN KEY (conversation_reference_id)
        REFERENCES conversation_references(conversation_reference_id),
    CHECK (expires_at_utc > created_at_utc),
    CHECK (
        (status = 'ACTIVE' AND finalized_at_utc IS NULL)
        OR (status IN ('RELEASED', 'EXPIRED') AND finalized_at_utc IS NOT NULL)
    )
);

CREATE UNIQUE INDEX ux_slot_holds_active_source_slot
ON slot_holds(source_slot_id)
WHERE status = 'ACTIVE';

CREATE INDEX ix_slot_holds_source_shipment
ON slot_holds(source_shipment_id, status);

CREATE TABLE idempotency_records (
    actor_subject TEXT NOT NULL,
    operation TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    PRIMARY KEY (actor_subject, operation, idempotency_key)
);

CREATE TABLE audit_events (
    audit_event_id TEXT PRIMARY KEY,
    occurred_at_utc TEXT NOT NULL,
    actor_subject TEXT NOT NULL,
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    correlation_id TEXT,
    payload_json TEXT NOT NULL,
    state_hold_id TEXT,
    FOREIGN KEY (state_hold_id) REFERENCES slot_holds(hold_id)
);

CREATE INDEX ix_audit_events_entity
ON audit_events(entity_type, entity_id, occurred_at_utc);
