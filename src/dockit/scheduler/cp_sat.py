"""Bounded CP-SAT proposals for a blocked deterministic scheduling request."""

from __future__ import annotations

from ortools.sat.python import cp_model

from dockit.scheduler.feasibility import DeterministicFeasibility
from dockit.scheduler.models import (
    FeasibilityStatus,
    OptimizationResult,
    OptimizationStatus,
    SchedulingSnapshot,
    SlotAssignment,
    priority_score,
)


class CpSatProposalOptimizer:
    """Find an approval-required rescheduling proposal; never execute it."""

    def __init__(self, *, max_time_seconds: float = 1.0) -> None:
        if max_time_seconds <= 0:
            raise ValueError("max_time_seconds must be positive")
        self._max_time_seconds = max_time_seconds
        self._evaluator = DeterministicFeasibility()

    def propose(self, snapshot: SchedulingSnapshot, shipment_id: str) -> OptimizationResult:
        """Use CP-SAT only when direct automatic feasibility cannot serve the request."""

        direct = self._evaluator.evaluate(snapshot, shipment_id)
        if direct.status is FeasibilityStatus.FEASIBLE:
            option = direct.options[0]
            return OptimizationResult(
                status=OptimizationStatus.DIRECT,
                assignments=(SlotAssignment(shipment_id, option.slot_id, None, False),),
                unassigned_shipment_ids=(),
                assessments=direct.assessments,
                requires_warehouse_approval=False,
                requires_affected_driver_approval=False,
            )
        if direct.status is FeasibilityStatus.ETA_REVIEW_REQUIRED:
            return OptimizationResult(
                status=OptimizationStatus.ETA_REVIEW_REQUIRED,
                assignments=(),
                unassigned_shipment_ids=(shipment_id,),
                assessments=direct.assessments,
                requires_warehouse_approval=False,
                requires_affected_driver_approval=False,
            )

        target = snapshot.shipment(shipment_id)
        movable = tuple(
            appointment
            for appointment in snapshot.appointments
            if appointment.status in {"PENDING_CONFIRMATION", "CONFIRMED"}
            and priority_score(appointment.priority) < priority_score(target.priority)
        )
        if not movable:
            return OptimizationResult(
                status=OptimizationStatus.NO_SOLUTION,
                assignments=(),
                unassigned_shipment_ids=(shipment_id,),
                assessments=direct.assessments,
                requires_warehouse_approval=False,
                requires_affected_driver_approval=False,
            )

        reclaimable = frozenset(appointment.appointment_id for appointment in movable)
        optimizable = (target, *(snapshot.shipment(item.shipment_id) for item in movable))
        assessments = tuple(
            assessment
            for shipment in optimizable
            for assessment in (
                self._evaluator.assess_slot(
                    snapshot,
                    shipment,
                    slot,
                    reclaimable_appointment_ids=reclaimable,
                )
                for slot in snapshot.slots
            )
        )
        candidates = {
            shipment.shipment_id: tuple(
                slot
                for slot in snapshot.slots
                if not self._evaluator.assess_slot(
                    snapshot,
                    shipment,
                    slot,
                    reclaimable_appointment_ids=reclaimable,
                ).reasons
                and not self._evaluator.assess_slot(
                    snapshot,
                    shipment,
                    slot,
                    reclaimable_appointment_ids=reclaimable,
                ).requires_operations_approval
            )
            for shipment in optimizable
        }
        if any(not available for available in candidates.values()):
            return OptimizationResult(
                status=OptimizationStatus.NO_SOLUTION,
                assignments=(),
                unassigned_shipment_ids=(shipment_id,),
                assessments=assessments,
                requires_warehouse_approval=False,
                requires_affected_driver_approval=False,
            )

        model = cp_model.CpModel()
        variables = {
            (shipment.shipment_id, slot.slot_id): model.new_bool_var(
                f"assign_{shipment.shipment_id}_{slot.slot_id}"
            )
            for shipment in optimizable
            for slot in candidates[shipment.shipment_id]
        }
        for shipment in optimizable:
            model.add_exactly_one(
                variables[(shipment.shipment_id, slot.slot_id)]
                for slot in candidates[shipment.shipment_id]
            )
        for slot in snapshot.slots:
            slot_variables = [
                variable
                for (candidate_shipment, candidate_slot), variable in variables.items()
                if candidate_slot == slot.slot_id
            ]
            if slot_variables:
                model.add_at_most_one(slot_variables)

        slot_rank = {slot.slot_id: index for index, slot in enumerate(snapshot.slots)}
        current_slot = {item.shipment_id: item.slot_id for item in movable}
        target_cost = sum(
            slot_rank[slot.slot_id] * 1_000_000 * variables[(target.shipment_id, slot.slot_id)]
            for slot in candidates[target.shipment_id]
        )
        movement_cost = sum(
            10_000 * variables[(shipment_id, slot.slot_id)]
            for shipment_id, original_slot_id in current_slot.items()
            for slot in candidates[shipment_id]
            if slot.slot_id != original_slot_id
        )
        downstream_cost = sum(
            slot_rank[slot.slot_id] * variables[(shipment.shipment_id, slot.slot_id)]
            for shipment in optimizable[1:]
            for slot in candidates[shipment.shipment_id]
        )
        model.minimize(target_cost + movement_cost + downstream_cost)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self._max_time_seconds
        status = solver.solve(model)
        if status == cp_model.UNKNOWN:
            return OptimizationResult(
                status=OptimizationStatus.TIME_LIMIT,
                assignments=(),
                unassigned_shipment_ids=(shipment_id,),
                assessments=assessments,
                requires_warehouse_approval=False,
                requires_affected_driver_approval=False,
            )
        if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
            return OptimizationResult(
                status=OptimizationStatus.NO_SOLUTION,
                assignments=(),
                unassigned_shipment_ids=(shipment_id,),
                assessments=assessments,
                requires_warehouse_approval=False,
                requires_affected_driver_approval=False,
            )

        appointment_by_shipment = {item.shipment_id: item for item in movable}
        assignments: list[SlotAssignment] = []
        for shipment in optimizable:
            selected_slot_id = next(
                slot.slot_id
                for slot in candidates[shipment.shipment_id]
                if solver.value(variables[(shipment.shipment_id, slot.slot_id)])
            )
            appointment = appointment_by_shipment.get(shipment.shipment_id)
            assignments.append(
                SlotAssignment(
                    shipment_id=shipment.shipment_id,
                    slot_id=selected_slot_id,
                    appointment_id=appointment.appointment_id if appointment is not None else None,
                    is_reschedule=(
                        shipment.shipment_id in current_slot
                        and selected_slot_id != current_slot[shipment.shipment_id]
                    ),
                )
            )
        has_reschedule = any(assignment.is_reschedule for assignment in assignments)
        return OptimizationResult(
            status=OptimizationStatus.PROPOSAL_READY,
            assignments=tuple(assignments),
            unassigned_shipment_ids=(),
            assessments=assessments,
            requires_warehouse_approval=has_reschedule,
            requires_affected_driver_approval=has_reschedule,
        )

    def allocate_batch(
        self,
        snapshot: SchedulingSnapshot,
        shipment_ids: tuple[str, ...],
    ) -> OptimizationResult:
        """Allocate competing new exceptions by priority, yard status, ETA, then slot time."""

        shipments = tuple(snapshot.shipment(shipment_id) for shipment_id in shipment_ids)
        if any(shipment.eta_confidence != "HIGH" for shipment in shipments):
            return OptimizationResult(
                status=OptimizationStatus.ETA_REVIEW_REQUIRED,
                assignments=(),
                unassigned_shipment_ids=shipment_ids,
                assessments=tuple(
                    assessment
                    for shipment in shipments
                    for assessment in self._evaluator.evaluate(
                        snapshot,
                        shipment.shipment_id,
                    ).assessments
                ),
                requires_warehouse_approval=False,
                requires_affected_driver_approval=False,
            )

        evaluations = {
            shipment.shipment_id: self._evaluator.evaluate(snapshot, shipment.shipment_id)
            for shipment in shipments
        }
        assessments = tuple(
            assessment
            for evaluation in evaluations.values()
            for assessment in evaluation.assessments
        )
        candidates = {
            shipment.shipment_id: evaluation.options
            for shipment, evaluation in (
                (shipment, evaluations[shipment.shipment_id]) for shipment in shipments
            )
            if evaluation.status is FeasibilityStatus.FEASIBLE
        }
        model = cp_model.CpModel()
        variables = {
            (shipment_id, option.slot_id): model.new_bool_var(
                f"assign_{shipment_id}_{option.slot_id}"
            )
            for shipment_id, options in candidates.items()
            for option in options
        }
        for shipment in shipments:
            shipment_variables = [
                variable
                for (candidate_shipment, _), variable in variables.items()
                if candidate_shipment == shipment.shipment_id
            ]
            if shipment_variables:
                model.add_at_most_one(shipment_variables)
        for slot in snapshot.slots:
            slot_variables = [
                variable
                for (_, candidate_slot), variable in variables.items()
                if candidate_slot == slot.slot_id
            ]
            if slot_variables:
                model.add_at_most_one(slot_variables)
        if not variables:
            return OptimizationResult(
                status=OptimizationStatus.NO_SOLUTION,
                assignments=(),
                unassigned_shipment_ids=shipment_ids,
                assessments=assessments,
                requires_warehouse_approval=False,
                requires_affected_driver_approval=False,
            )

        eta_rank = {
            shipment.shipment_id: rank
            for rank, shipment in enumerate(sorted(shipments, key=lambda item: item.eta_at))
        }
        slot_rank = {slot.slot_id: rank for rank, slot in enumerate(snapshot.slots)}
        objective = sum(
            (
                priority_score(shipment.priority) * 1_000_000
                + (100_000 if shipment.is_waiting else 0)
                + (len(shipments) - eta_rank[shipment.shipment_id]) * 1_000
                + (len(snapshot.slots) - slot_rank[option.slot_id])
            )
            * variables[(shipment.shipment_id, option.slot_id)]
            for shipment in shipments
            for option in candidates.get(shipment.shipment_id, ())
        )
        model.maximize(objective)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self._max_time_seconds
        status = solver.solve(model)
        if status == cp_model.UNKNOWN:
            return OptimizationResult(
                status=OptimizationStatus.TIME_LIMIT,
                assignments=(),
                unassigned_shipment_ids=shipment_ids,
                assessments=assessments,
                requires_warehouse_approval=False,
                requires_affected_driver_approval=False,
            )
        if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
            return OptimizationResult(
                status=OptimizationStatus.NO_SOLUTION,
                assignments=(),
                unassigned_shipment_ids=shipment_ids,
                assessments=assessments,
                requires_warehouse_approval=False,
                requires_affected_driver_approval=False,
            )
        assignments = tuple(
            SlotAssignment(shipment.shipment_id, option.slot_id, None, False)
            for shipment in shipments
            for option in candidates.get(shipment.shipment_id, ())
            if solver.value(variables[(shipment.shipment_id, option.slot_id)])
        )
        assigned_ids = {assignment.shipment_id for assignment in assignments}
        unassigned = tuple(
            shipment.shipment_id
            for shipment in shipments
            if shipment.shipment_id not in assigned_ids
        )
        return OptimizationResult(
            status=(
                OptimizationStatus.PROPOSAL_READY
                if not unassigned
                else OptimizationStatus.PARTIAL
            ),
            assignments=assignments,
            unassigned_shipment_ids=unassigned,
            assessments=assessments,
            requires_warehouse_approval=False,
            requires_affected_driver_approval=False,
        )
