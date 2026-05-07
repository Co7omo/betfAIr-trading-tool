# Product Requirements Document (PRD)

Cross-reference: `../architecture/00-overview.md`, `kpis-and-metrics.md`, `backtesting-protocol.md`.

## Product Goal
Deliver an MVP for pre-match AI-assisted trading on Betfair Exchange football Match Odds (1X2), with full auditability and controlled risk.

## Users
- Primary: Quant/trading operator.
- Secondary: ML engineer, risk/compliance reviewer.

## Problem Statement
Market prices are often efficient, but temporary pre-match inefficiencies can occur. The system must identify net-positive edge opportunities while controlling execution and risk.

## Scope (MVP)
- Pre-match only, window T-120 to T-10.
- Market: Match Odds 1X2.
- Signal baseline: market + Elo + form.
- Execution: Betfair Exchange API.
- Full audit chain required.

## Out of Scope
- In-play trading.
- Multi-sport expansion.
- Complex hedging automation.

## Core Requirements
1. Discover and monitor eligible markets.
2. Build as-of feature vectors.
3. Infer calibrated probabilities.
4. Apply edge/risk policy.
5. Execute and reconcile orders.
6. Compute settlement-based net P&L.
7. Persist append-only audit events.

## Success Criteria
- Predictive quality above market-only baseline.
- Positive net ROI in out-of-time evaluation.
- No uncontrolled risk limit breaches.
- 100% traceability for sampled audits.

## Open Questions
- Final commission configuration by account tier.
- Minimum liquidity thresholds by league tier.
- Operator-approved fallback mode policy.

## Checklist
- [ ] Scope and non-goals signed off.
- [ ] KPIs have target/guardrail values.
- [ ] Runbook accepted by operations owner.

## References
- Betfair Exchange API reference
- Betfair Data Scientists guide