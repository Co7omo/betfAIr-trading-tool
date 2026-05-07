# Architecture Overview — Betfair Exchange AI Trading MVP

## Purpose
This document provides a concise architectural map of the MVP and links to detailed specifications.

## Scope (MVP Default)
- Domain: Betfair Exchange
- Sport: Football
- Market: Match Odds (1X2)
- Trading mode: **Pre-match only**
- Trading window (default MVP): **T-120 min to T-10 min**
- Polling interval (default MVP): **10 seconds**

## Non-Goals
- No in-play trading
- No multi-market portfolio optimization beyond Match Odds 1X2
- No autonomous strategy class switching in MVP

## Documentation Table of Contents
1. [01-requirements-nfrs.md](./01-requirements-nfrs.md) — Functional and non-functional requirements.
2. [02-c4-context.md](./02-c4-context.md) — C4 Context diagram and boundaries.
3. [03-c4-container.md](./03-c4-container.md) — C4 Container architecture.
4. [04-c4-component.md](./04-c4-component.md) — Component-level decomposition.
5. [05-sequence-diagrams.md](./05-sequence-diagrams.md) — Core runtime sequences.
6. [06-data-model.md](./06-data-model.md) — Audit-first data model and retention.
7. [07-risk-controls.md](./07-risk-controls.md) — Risk policy, limits, and controls.
8. [08-observability.md](./08-observability.md) — Metrics, logs, traces, alerts.
9. [09-failure-modes-runbook.md](./09-failure-modes-runbook.md) — Incident handling and runbooks.
10. [10-mvp-roadmap.md](./10-mvp-roadmap.md) — Delivery phases and milestones.
11. [adr-0001-scope-prematch-only.md](./adr-0001-scope-prematch-only.md)
12. [adr-0002-signal-supervised-elo-form.md](./adr-0002-signal-supervised-elo-form.md)
13. [adr-0003-bet-settlement-vs-hedge.md](./adr-0003-bet-settlement-vs-hedge.md)

## MVP Architectural Principles
- **Auditability first**: append-only event records for decisions, orders, fills, settlement, and bankroll snapshots.
- **Determinism where possible**: decision reproducibility through model/feature version pinning.
- **Fail-safe operations**: kill switch and safe-stop as first-class controls.
- **Operational simplicity**: minimal moving parts for initial production learning.

## Checklist
- [ ] Pre-match scope enforced by configuration and runtime guards.
- [ ] Decision engine applies edge, liquidity, spread, and risk filters.
- [ ] All orders use stable `customerOrderRef` and reconciliation lifecycle.
- [ ] Settlement and P&L are traceable from event-level to bankroll-level.
- [ ] Cross-document assumptions are consistent.

## References
- Betfair Data Scientists guide
- Betfair Exchange API reference (`listMarketBook`, `placeOrders`, `listMarketCatalogue`, `listCurrentOrders`, `listClearedOrders`)
- Betfair Historical Data Services API
- Betfair Commission documentation