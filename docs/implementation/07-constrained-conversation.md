# 07 — Constrained conversation

**Status:** complete for the constrained service boundary  
**Completed:** 2026-11-27  
**Owner:** Codex  
**Implementation-plan step:** Step 7 in `docs/IMPLEMENTATION_PLAN.md`

## Scope, roadmap items, and acceptance criteria

Implement a multi-turn conversation boundary that extracts structured intent
and entities, asks for missing facts, stores only principal-bound TTL state,
invokes an allow-listed typed workflow tool, presents its trusted result, and
requires explicit confirmation before a held option can be submitted.

The conversation layer must not access SQL, decide capacity, bypass service
authorization, claim that a hold is a booking, or treat prompt-injection text
as policy. Redis and inference failures must have a safe degraded response.

## Relevant chat context and confirmed decisions

- Steps 1–6 were complete before this step began.
- The user authorized proceeding after resolving the earlier working-tree
  changes; the pre-existing `.env.example` edit was preserved.
- The service/domain layer remains authoritative. Conversation code can only
  call typed tools supplied by the service composition root.
- The AgentCore model/runtime and failure policy remain unresolved Step 0
  operational configuration, so the implementation adds an injected AgentCore
  client boundary and does not deploy or contact AWS.

## Facts versus assumptions

### Facts

- `Principal` already carries the Cognito subject, groups, and linked driver ID.
- The repository already depends on Redis, LangChain, boto3, Pydantic, and
  PyJWT.
- The AgentCore Harness reference uses the `bedrock-agentcore` data-plane
  client, `invoke_harness`, a runtime session ID of at least 33 characters, and
  a streamed response.
- Full validation passed: 61 tests, Ruff, mypy, and editor diagnostics.

### Assumptions

- DockIt is currently single-tenant, represented by the explicit default
  tenant ID `setuhaul`; the key and state schema retain a tenant boundary for
  future tenancy decisions.
- The supplied typed workflow tools return JSON-serializable mappings and own
  authorization, idempotency, capacity checks, and final booking revalidation.
- The model adapter is untrusted even when it runs inside AgentCore; all model
  output is validated before it can affect a tool call.

## Design, contract, state, migration, and security/operational impact

- `ConversationIntent` is a closed-world Pydantic contract with bounded IDs,
  UTC-normalized ETA values, delay facts, hold IDs, and an explicit confirmation
  flag. Unknown fields and malformed model JSON are rejected.
- `ConversationOrchestrator` requires an enrolled driver principal, merges
  missing facts across turns, asks deterministic clarification questions, and
  selects tools only from the supplied action allow-list.
- Confirmation requires an active conversation hold, matching hold ID, and a
  plain-language explicit affirmative. The orchestrator never performs a
  booking mutation itself.
- `ConversationState` binds tenant, Cognito subject, driver, and thread. The
  Redis key is a SHA-256 digest rather than raw identity data and uses the
  configured TTL.
- `AgentCoreChatModel` accepts an injected SigV4-authenticated boto3-compatible
  client, validates the session length, consumes streamed text events, and
  converts SDK/runtime failures into the safe conversation fallback.
- No migration was required. Conversation messages remain outside the
  authoritative database; the existing conversation-reference table remains
  the persistence boundary for later API wiring.

## Changed files and symbols

| File | Symbols or change |
| --- | --- |
| `src/dockit/agent/orchestration.py` | Typed intent/state contracts, Redis binding, typed tool protocol, constrained orchestrator, OpenRouter adapter hardening, AgentCore Harness adapter. |
| `tests/test_orchestration.py` | Multi-turn, injection, confirmation, authorization, outage, schema, Redis, and AgentCore stream coverage. |
| `docs/IMPLEMENTATION_PLAN.md` | Step 7 status, validation evidence, and Step 8 hand-off. |
| `docs/implementation/README.md` | Step 7 completion index entry. |

## Tool/command ledger

| Tool/action | Sanitized command/action | Purpose | Result |
| --- | --- | --- | --- |
| `exec_command` | Read Step 7 plan, existing orchestration, contracts, workflows, and settings | Establish the current boundary and avoid duplicating service policy. | Confirmed the previous wrapper accepted free-form workflow results and lacked structured intent/tool enforcement. |
| `exec_command` | Read local AgentCore Runtime and Harness references | Verify the data-plane client, invocation method, session requirement, and stream shape. | Confirmed `bedrock-agentcore`, `invoke_harness`, 33-character session minimum, and streamed content deltas. |
| `apply_patch` | Replaced the constrained orchestration module and added focused tests | Implement Step 7 without changing booking or database authority. | Applied successfully. |
| `pytest` | `.venv/bin/python -m pytest -q tests/test_orchestration.py` | Run focused conversation validation. | 9 passed. |
| `ruff` | `.venv/bin/ruff check .` | Validate formatting and lint rules. | Passed. |
| `mypy` | `.venv/bin/mypy src` | Validate strict source typing. | Passed for 30 source files. |
| `pytest` | `.venv/bin/python -m pytest -q` | Validate the complete repository. | 61 passed; 17 third-party LangSmith deprecation warnings. |
| `problems` | Checked the changed Python files | Check editor diagnostics. | No errors found. |

## Tests and validation evidence

- A first turn with missing shipment facts is persisted as clarification state;
  a second turn supplies the missing entities and invokes the typed tool.
- Prompt-injection text produces no tool call and cannot override the policy.
- Confirmation is blocked without an active matching hold and an explicit
  affirmative message.
- Unenrolled/non-driver principals are rejected before model or tool execution.
- Invalid model JSON, malformed Redis state, model outages, Redis outages, and
  AgentCore stream failures do not produce operational claims.
- A composition-model outage after a typed operation commits returns a
  deterministic status-check message instead of asking the user to repeat a
  potentially mutating request.
- Redis TTL and hashed principal/thread binding are verified.
- AgentCore streamed content is collected only from typed content deltas.

## Limitations, risks, follow-up decisions, Git state, and exact next step

- The actual AgentCore Harness ARN, runtime model, timeout policy, IAM policy,
  and deployment target remain open operational decisions. The adapter is
  injectable and unconnected in local tests.
- The current tenant default is intentionally explicit and must be replaced or
  expanded when the Step 0 tenancy decision is confirmed.
- The composition root/API does not yet expose conversation endpoints; that is
  the Step 8 scope.
- The macOS `git` executable was unavailable because Command Line Tools are not
  installed. A read-only index/worktree comparison confirmed the pre-existing
  `.env.example` edit plus the Step 7 source, test, and documentation changes;
  no commit was created by this step.

**Exact next step:** implement Step 8 versioned HTTP endpoints and the
Streamlit pilot UI over the authenticated, typed workflow and conversation
boundaries.
