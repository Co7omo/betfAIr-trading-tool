# ADR-0002: Supervised Signal with Market + Elo + Form Baseline

- Status: Accepted
- Date: 2026-03-10
- Deciders: ML + Trading Engineering

## Context
The MVP needs a pragmatic, testable signal with low feature acquisition complexity and clear anti-leakage rules.

## Decision
Use supervised probability estimation for 1X2 with staged feature sets:
- A0: market-only features.
- A1: market + Elo.
- A2: market + Elo + form (N=5, N=10).

## Rationale
- Market features capture consensus and liquidity state.
- Elo gives robust long-term strength prior.
- Form adds short-term dynamics.

## Constraints
- All features computed as-of decision timestamp.
- Strict temporal split for evaluation.
- Feature/version pinning in audit records.

## Consequences
### Positive
- Incremental ablation path with measurable gains.
- Operationally manageable external data requirements.

### Negative
- Model may underfit contextual effects (injuries/news) not included in MVP.

## Cross-References
- `04-c4-component.md`
- `docs/product/experiment-plan-ablation.md`
- `docs/product/backtesting-protocol.md`

## References
- Betfair Data Scientists guide
- Betfair Historical Data Services API