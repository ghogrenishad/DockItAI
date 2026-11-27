"""Authoritative revalidation of a selected slot against deterministic policy."""

from __future__ import annotations

from datetime import UTC, datetime

from dockit.db.source_repository import SourceRepository
from dockit.scheduler.feasibility import DeterministicFeasibility
from dockit.scheduler.source_adapter import load_snapshot_with_rules


class SlotNotFeasible(RuntimeError):
    """Raised when a selected slot no longer satisfies deterministic policy."""


class SlotFeasibilityRevalidator:
    """Re-read source facts and reject invalid or approval-only selections."""

    def __init__(self, repository: SourceRepository) -> None:
        self._repository = repository

    def validate(self, *, shipment_id: str, slot_id: str) -> None:
        facility_id = self._repository.get_shipment_facility(shipment_id)
        if facility_id is None:
            raise SlotNotFeasible("shipment does not exist")
        snapshot = load_snapshot_with_rules(
            self._repository,
            facility_id,
            observed_at=datetime.now(UTC),
        )
        result = DeterministicFeasibility().evaluate(snapshot, shipment_id)
        selected = next((option for option in result.options if option.slot_id == slot_id), None)
        if selected is None:
            raise SlotNotFeasible("selected slot is no longer feasible")
        if selected.requires_operations_approval:
            raise SlotNotFeasible("selected slot requires operations approval")
