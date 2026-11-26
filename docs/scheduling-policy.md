# DockIt deterministic scheduling policy

## Boundary

The scheduler reads a point-in-time SetuHaul snapshot and returns options or
approval-required proposals. It never writes a source appointment, creates a
booking, sends a message, or applies a reschedule. Those effects remain in
later workflow and integration steps.

## Straightforward deterministic search

For one shipment, the evaluator considers each facility slot and rejects it
with explicit reasons when any hard constraint fails:

- ETA confidence is not `HIGH`;
- the slot is occupied, blocked, or closed;
- the slot begins before the reliable ETA;
- dock type, refrigeration, or dock weight is incompatible;
- the slot cannot contain the entire expected unload duration;
- a blocking dock event overlaps the slot; or
- a heavy load exceeds the facility heavy-dock threshold without a heavy dock.

Eligible automatic options are ranked by earliest start, then earliest end,
dock code, and slot ID. A low-confidence ETA does not receive automatic
capacity; it receives an ETA-review outcome.

## Hours, overrun, and escalation

- An option whose start is after a facility's last-start rule or whose unload
  would end after facility close is an operations-approval option, not an
  automatic result.
- A single appointment slot must contain the full expected unload duration;
  DockIt does not combine contiguous slots or plan an overrun.
- Once unloading begins, the truck remains at that dock until completion. The
  scheduler treats the dock as unavailable through expected completion and any
  overlapping dock-event end. An event with no end blocks the dock and
  escalates.
- If no automatic option exists, the result is a reasoned no-solution or
  operations-approval outcome; capacity is never invented.

## Bounded CP-SAT proposals

CP-SAT is a fallback for contention or a blocked direct request. It creates a
yes/no decision for every feasible shipment-to-slot pairing, then enforces:

- at most one slot per shipment;
- at most one shipment per slot; and
- every deterministic hard constraint above.

For a batch of new exceptions, the objective is priority first
(`CRITICAL > HIGH > NORMAL > LOW`), then physical yard presence, then earlier
reliable ETA, then earlier slot. The solver runs with a one-second limit.

For a blocked high-priority request, CP-SAT may propose moving only a strictly
lower-priority `PENDING_CONFIRMATION` or `CONFIRMED` appointment. `IN_PROGRESS`
and completed work remain fixed. Any move is an approval package requiring the
warehouse and the affected driver; it is never executed automatically.
