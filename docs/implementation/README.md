# DockIt implementation record protocol

This directory is the durable hand-off history for completed implementation steps. It supports later context recovery without retaining secrets, tokens, PII, private database rows, or hidden instructions.

## Rules

1. Keep this index current.
2. After implementing and validating a numbered plan step, add exactly one record named NN-short-slug.md, for example 01-application-foundation.md.
3. An active record may be updated. A completed record is immutable history; a later record corrects or supersedes it with a link back.
4. Summarize relevant user decisions and chat context; never copy credentials, JWTs, PII, hidden prompts, or raw production data.
5. The tool ledger must record the tool/action, sanitized command/action, purpose, and result. Redact credentials, authorization headers, tokenized URLs, and sensitive identifiers.

## Required record sections

- Status, completion time, owner, and implementation-plan step.
- Scope, roadmap items, and acceptance criteria.
- Relevant chat context and confirmed decisions.
- Facts versus assumptions.
- Design, contract, state, migration, and security/operational impact.
- Changed files and symbols.
- Tool/command ledger: tool/action, sanitized command/action, purpose, result.
- Tests/validation evidence and output summary.
- Limitations, risks, follow-up decisions, Git state/artifacts, and exact next step.

## Status index

| Plan step | Status | Completion record | Blocker/dependency |
| --- | --- | --- | --- |
| 0 — Roadmap reconciliation | in progress | — | Booking authority, scheduling/security policy, escalation owner, deployment, and pilot decisions remain unresolved; tracked source contract is established |
| 1 — Foundation | complete | [01 — Application foundation](./01-application-foundation.md) | Safe to complete independently; booking/integration work remains gated by Step 0 |
| 2 — Persistence | complete | [02 — Persistence and source-data boundary](./02-persistence-source-data.md) | Safe seed-data access confirmed; booking authority remains a Step 0 decision |
| 3 — Contracts/policy | complete | [03 — Contracts, policy, and state machine](./03-contracts-policy-state-machine.md) | Local-only policy complete; booking authority remains a Step 0/5 decision |
| 4 — Feasibility | complete | [04 — Deterministic feasibility](./04-deterministic-feasibility.md) | Proposal-only CP-SAT complete; booking authority remains a Step 0/5 decision |
| 5 — Workflows | pending | — | Steps 0–4 |
| 6 — Identity/protections | pending | — | Step 0 and workflow contracts |
| 7 — Conversation | pending | — | Steps 4–6 |
| 8 — API/UI | pending | — | Steps 5–7 |
| 9 — Operations/deployment | pending | — | Target decision and prior service steps |
| 10 — Acceptance | pending | — | All applicable delivery steps |
