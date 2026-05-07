# Security and Privacy

Cross-reference: `../architecture/08-observability.md`, `../architecture/09-failure-modes-runbook.md`.

## Security Objectives
- Protect API credentials and trading control paths.
- Ensure integrity of audit records.
- Limit blast radius of compromised components.

## Identity and Access
- Least-privilege roles by component.
- Segregation of duties: operator controls vs engineering access.
- Mandatory key/token rotation policy.

## Secrets Management
- Store credentials in managed secret storage.
- No secrets in logs, configs, or source-controlled artifacts.
- Access audited and time-bounded.

## Data Integrity
- Append-only ledger with immutable event records.
- Hash/checksum validation for critical payloads.
- Signed deployment artifacts for model/config releases.

## Network and Runtime Controls
- Restrict outbound access to required providers only.
- Use encrypted transport for all external APIs.
- Runtime hardening and dependency patch cadence.

## Privacy Considerations
- MVP primarily uses market and sports event data.
- If any personal data appears in operational logs, minimize and redact by policy.
- Define retention and deletion policy aligned with legal requirements.

## Incident Security Playbook
- Credential compromise => immediate revoke/rotate + kill switch.
- Unauthorized config change => rollback to last signed config snapshot.
- Audit integrity concern => forensic export and read-only lockdown.

## Checklist
- [ ] Secrets never exposed in application telemetry.
- [ ] Access logs reviewed periodically.
- [ ] Security incidents map to runbook procedures.

## References
- Betfair Exchange API reference
- Betfair Commission documentation