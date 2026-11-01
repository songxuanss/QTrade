# Supplier rights-issue coverage

## Status

The connected supplier (MaiRui) does **not** publish rights-issue (配股) terms.
Verified on 2026-11-01 against the live licence:

| Check | Result |
| --- | --- |
| `hscp/jnfh/{code}` for 600519 / 600036 / 601288 | HTTP 200, fields `sdate,rdate,give,change,send,line,cdate,edate,hdate` — cash dividend, bonus and transfer only |
| `hscp/jnpg/600036` | HTTP 422 `path or params not served at edge` |
| `hscp/pg/600036` | HTTP 422 `path or params not served at edge` |
| `hscp/jnpz/600036` | HTTP 422 `path or params not served at edge` |
| Documented corporate-action endpoints | `hscp/jnfh` (dividends/bonus/transfer) and `hscp/jnzf` (seasoned offering); no rights endpoint |

Evidence: `.tools/verification-local/review/rights-feed-probe.json`.

## What this means

- A real rights feed cannot be connected with the current credentials and
  supplier contract. This is a **supplier coverage gap**, not a missing code
  path: the Ledger already supports `subscribe`/`skip`, cash-capped
  subscription, booked `rights_subscription` fills, manifest freezing and
  fail-closed rejection of incomplete terms.
- The source profile now exposes `extension.rights_terms` with
  `state=not_connected` and the probe outcome, so the gap is visible in the
  catalog instead of being implied by silent absence.
- No fixture rights event is presented as a live integration.

## Alternative sources

A verified comparison of free and commercial providers is in
[`docs/data/RIGHTS-DATA-SOURCES.md`](RIGHTS-DATA-SOURCES.md). Sina Finance
(via AkShare) does publish historical rights terms and can be integrated as a
dedicated adapter.

## Unblock

Any one of:

1. a supplier with a historical rights-issue endpoint (and entitlement);
2. an approved official disclosure source together with its usage terms;
3. a decision to treat seasoned offerings (`hscp/jnzf`, real supplier data) as
   the next best corporate-action capability.

Until then, rights acceptance stays on a fixture with explicit limitations in
`docs/testing/BACKTEST-COVERAGE.md`.
