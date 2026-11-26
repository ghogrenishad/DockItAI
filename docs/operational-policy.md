# DockIt operational policy contract

## Scope and status

This is the confirmed Step 3 policy used by the shared Pydantic contracts. It
is deliberately local-only: it does not write SetuHaul source data or claim
that a warehouse booking exists. Booking authority still requires a product-
owner decision before Step 5 implements a booking workflow.

## Time policy

- Every persisted or exchanged operational instant is timezone-aware and
  normalized to UTC by the contract (`*_at_utc`). Naive timestamps are invalid.
- Facility display pairs a UTC instant with a validated IANA timezone such as
  `Asia/Kolkata`; local display is derived, never stored as an authority.
- Source adapters must convert the source's offset-bearing timestamps at their
  boundary. Scheduling and workflow layers receive the UTC contracts only.

## Ownership and idempotency

- Until Cognito claims arrive in Step 6, actor ownership means exact equality
  of opaque `actor_subject` values. A caller cannot mutate a record owned by a
  different subject.
- An idempotency identity is `(actor_subject, operation, key)`, matching the
  `idempotency_records` primary key. Every retry also carries a request
  fingerprint; Step 5 must reject the same identity with a different
  fingerprint rather than repeat or reinterpret the mutation.
- The API error envelope has stable codes and an optional correlation ID. API
  routes are added in Step 8, but their error payload must preserve this shape.

## Local holds and revalidation

- A local hold starts exactly when the options are successfully provided to the
  end user in chat. `options_provided_at_utc` is the authoritative start time,
  not the time a later user response is received.
- The hold expires exactly five minutes (`300` seconds) after that timestamp.
  At the cutoff, the system releases capacity by transitioning the active hold
  to `EXPIRED`; it must not wait for a chat response.
- At two minutes thirty seconds (`150` seconds), if the hold remains active and
  no slot-selection response has been processed, the LLM sends one reminder to
  select a slot. `reminder_sent_at_utc` makes this reminder idempotent: retries
  must not prompt repeatedly.
- A hold is `ACTIVE`, `RELEASED`, or `EXPIRED`. It is never a SetuHaul
  appointment and never warehouse confirmation.
- Before any future confirmation, the workflow must revalidate the current
  source state, revalidate the active local hold, and use the selected
  authoritative booking system. All three requirements are non-optional.

## Local exception state machine

```text
REPORTED → TRIAGED → ALTERNATIVES_FOUND → PROPOSAL_PENDING_CONFIRMATION → HELD
                                                                        ├→ EXPIRED
                                                                        └→ CANCELLED

Any active decision point may also reach CANCELLED or REJECTED where shown by
the contract. Terminal states cannot transition further.
```

There is intentionally no `CONFIRMED` transition in this local state machine.
`ConfirmationRequest` represents a driver's explicit request only. Adding a
confirmed reservation state requires the Step 0 decision naming the
authoritative booking owner and the Step 5 transaction/reconciliation design.

## Contract ownership

The implementation is in `src/dockit/schemas/contracts.py` and
`src/dockit/schemas/policy.py`. Any new transition, idempotency scope, or
hold-lifecycle behavior must update the model, policy, tests, and this document
together.
