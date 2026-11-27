# 06 — Identity and protections

**Status:** complete  
**Completed:** 2026-11-27  
**Implementation-plan step:** Step 6

## Scope

Add Cognito access-token verification, group and driver ownership enforcement,
email-based one-time driver enrollment, baseline security headers, disabled
browser CORS, and a conservative request limiter.

## Confirmed decisions

- API clients send Cognito Bearer access tokens.
- Cognito groups are `drivers`, `warehouse`, and `operations`.
- A signup email is enrolled against the authoritative driver record once, then
  the immutable Cognito `sub` is used for every authorization decision.
- No browser origin is allowed until the pilot UI origin is approved.

## Implementation and evidence

- `security.py` verifies RS256 JWT signature, JWKS key ID, issuer, client ID,
  expiry, access-token use, groups, and linked-driver ownership.
- Migration 5 adds enrolled driver email plus the subject-to-driver link.
- Protected future routes use `require_principal`; no test-auth bypass exists.
- Security headers, rate limiting, and health-probe exclusion are exercised by
  system tests.
- `pytest -q`: 51 passed; Ruff and mypy passed for 29 source files.

## Remaining operational configuration

Deploy the Cognito user pool/app client, enroll driver emails, configure the
real JWKS reachability, and set the exact Streamlit origin before enabling CORS.

**Exact next step:** constrained conversational orchestration over typed
services only; it must never expose SQL or bypass this booking/authorization
boundary.
