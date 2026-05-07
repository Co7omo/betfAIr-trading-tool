# ADR-0003: Settlement-Based Realized P&L as Source of Truth (MVP)

- Status: Accepted
- Date: 2026-03-10
- Deciders: Trading Engineering + Risk

## Context
For MVP, introducing dynamic hedging adds strategy complexity and can obscure attribution of model edge vs execution tactics.

## Decision
Use settlement-based realized P&L as canonical metric for MVP evaluation.
- Reconciliation and accounting are settlement-centric.
- Hedging is out-of-scope for initial MVP (future phase candidate).

## Consequences
### Positive
- Clean attribution and simpler audit trail.
- Reduced execution logic complexity.

### Negative
- No active risk flattening via hedge in MVP.
- Potentially higher variance in outcomes.

## Guardrails
- Strict position caps and daily stop-loss compensate for no-hedge policy.
- One position per event.

## Cross-References
- `05-sequence-diagrams.md`
- `06-data-model.md`
- `07-risk-controls.md`

## References
- Betfair Exchange API reference (`listClearedOrders`)
- Betfair Commission documentation