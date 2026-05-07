# ADR-0001: Scope Limited to Pre-Match Only (MVP)

- Status: Accepted
- Date: 2026-03-10
- Deciders: Product + Engineering

## Context
In-play trading introduces materially higher latency sensitivity, microstructure volatility, and operational risk. The MVP goal is controlled learning with strong auditability.

## Decision
Limit MVP trading to pre-match window only:
- Default MVP window: T-120 to T-10.
- Exclude all in-play decisioning/execution paths.

## Consequences
### Positive
- Lower operational complexity.
- Easier reproducibility and analysis.
- Cleaner backtesting assumptions.

### Negative
- Reduced opportunity set.
- Potentially lower gross return ceiling.

## Verification
- Time guards at discovery, decision, and execution layers.
- Alerts for any attempted out-of-window action.

## Cross-References
- `00-overview.md`
- `01-requirements-nfrs.md`
- `07-risk-controls.md`

## References
- Betfair Exchange API reference