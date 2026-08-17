# PhantomLink Phase 2 Decision Record

**Status:** Approved for implementation planning on 2026-08-13  
**Approved design:** `../specs/2026-08-13-phantomlink-phase2-private-network-dashboard-design.md` at commit `af64499`

## Durable decisions

1. Continue PhantomLink as its own codebase. Empire is architecture inspiration only: durable registry, append-only audit history, and typed internal contracts.
2. Phase 2 is limited to device status, heartbeat, operator disconnect, and permanent revoke. It adds no remote command, tasking, shell, file-transfer, persistence, installer, updater, or endpoint-security evasion behavior.
3. Managed traffic is reachable only through an externally configured private network or VPN. PhantomLink binds an exact VPN address and uses mutual TLS; it does not install or configure the VPN.
4. Enrollment uses the existing pinned HTTPS bootstrap, a 600-second one-time token, and an agent-generated private key plus CSR. The agent private key never leaves that Windows account.
5. The controller uses a stdlib SQLite registry in WAL mode and stores no private keys, tokens, shared secrets, or DPAPI blobs in the database.
6. The Textual dashboard keeps the legacy view and adds a separate Managed Agents view. Managed state comes only from the registry plus the live session manager.
7. Phase 1 specifications, plans, runbooks, verification records, and rollback artifacts remain retained as historical evidence. A file is deleted only after proving it is both unused by code and superseded by a named retained document.
8. Decisions that change scope, trust boundaries, schemas, contracts, or operational limits are appended here with the date and affected design section. Routine implementation progress belongs in commits and verification records rather than this decision log.
9. Direct Empire code reuse requires a provenance note and preservation of BSD-3-Clause notices. Phase 2 has no runtime dependency on Empire.
10. The Phase 2 SQLite registry starts with public device metadata only. Existing Phase 1 `tokens.json` and `devices.bin` stores are preserved by verified byte-for-byte backup and are neither imported nor deleted; their legacy readers remain retained until the Task 8 production-import proof.
11. On 2026-08-13, the operator disconnect surface was resolved to Dashboard `D` backed by the controller's in-process `DeviceActionService`. There is no separate-process `managed_auth disconnect` command, IPC channel, or control API because a separate CLI process cannot own or close controller session sockets. `managed_auth revoke` remains a durable administrative operation: an already-connected session is rejected and closed on its next durable heartbeat authorization check, while Dashboard `R` performs the durable revoke and immediately closes the in-process live session.

## Known ceilings accepted for Phase 2

- SQLite is a single-controller registry; no cluster or multi-writer service is introduced.
- Audit history is append-only with no retention or export mechanism in this phase.
- The CA has no online rotation workflow; compromise requires a fresh CA and device re-enrollment.
- Windows Service installation, signed packaging, anti-tamper, and source-inspection resistance remain deferred.
- Initial two-machine acceptance starts processes manually after reboot.
