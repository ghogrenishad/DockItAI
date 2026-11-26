CREATE TABLE booking_proposals (
    proposal_id TEXT PRIMARY KEY,
    shipment_id TEXT NOT NULL,
    slot_id TEXT NOT NULL,
    hold_id TEXT,
    proposal_status TEXT NOT NULL
        CHECK (proposal_status IN ('PENDING_DRIVER', 'HELD', 'CONFIRMED', 'CANCELLED', 'EXPIRED')),
    requires_warehouse_approval INTEGER NOT NULL DEFAULT 0 CHECK (requires_warehouse_approval IN (0,1)),
    requires_affected_driver_approval INTEGER NOT NULL DEFAULT 0 CHECK (requires_affected_driver_approval IN (0,1)),
    created_at_utc TEXT NOT NULL,
    finalized_at_utc TEXT,
    FOREIGN KEY (hold_id) REFERENCES slot_holds(hold_id)
);

CREATE INDEX ix_booking_proposals_shipment_status
ON booking_proposals(shipment_id, proposal_status);
