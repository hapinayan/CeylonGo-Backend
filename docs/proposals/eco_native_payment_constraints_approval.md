# Native Eco payment constraint proposal

**Pending database approval.** The [proposed SQL](eco_native_payment_constraints.proposed.sql) is outside the migration inventory. It does not change migrations `001–040`, the archived Eco `030` proposal, or a shared payment constraint.

## Change and prerequisites

Migration `034` only allows native `unpaid` and `paid` states. Paid cancellation requires preserving the verified capture in the shared ledger while releasing capacity and marking the cancelled native reservation as `refund_pending` or `refund_rejected`. The proposal adds those states only to cancelled reservations. The existing native state/hold check and the shared booking and payment constraints remain in force. No historical rows need backfill.

Before assigning a migration number, authorize a read-only catalog and ledger review. Verify the exact deployed `034` definition, constraint trust, native row populations, canonical `001–040` checksums, and the status predicates in shared commerce tables. Stop on unexpected drift. The approved numbered version must execute within the migration runner's transaction. On an approved isolated target, rehearse trusted DDL, failed constraint validation and transaction rollback. Race paid cancellation against capture, replay, ordinary cancellation, and slot mutation.

Rollback requires reviewing any cancelled rows with the new payment states. If they exist, keep the expanded constraint until a separately approved preservation strategy is available. Do not rewrite or delete reservation or ledger history to force rollback.

## Release gates

- `ECO_NATIVE_PAYMENTS_ENABLED=1` enables new checkout only after shared PayHere merchant configuration and isolated SQL Server and sandbox acceptance. Existing callbacks can still reconcile an existing order if checkout is disabled.
- `ECO_NATIVE_PAID_CANCELLATION_ENABLED=1` requires the approved native constraint change to have been applied and verified, plus isolated acceptance of the cancellation/refund policy. Leave both flags unset during deployment preparation.
- Approval and rejection record a decision. A production refund verifier and verified execution reconciliation are separate release prerequisites. A PayHere chargeback callback, a browser return, or an Admin decision is insufficient evidence that a refund was sent.

## Phase 01B additions

The proposal adds dedicated Eco tables for trusted listing refund percentages, immutable per-booking percentages, listing age limits, slot-specific permit and guide clearance, and reconciliation evidence. The native reservation constraint ties all additional refund outcomes to cancelled bookings. The shared commerce constraints retain their existing definitions.

Keep `ECO_NATIVE_POLICY_SNAPSHOTS_ENABLED`, `ECO_NATIVE_SAFETY_RECORDS_ENABLED`, and `ECO_NATIVE_REFUND_RECONCILIATION_ENABLED` unset before schema acceptance. Authorize catalog checks for referenced tables and primary key types, then verify the filtered unique refund-reference index, all foreign keys, policy and clearance constraints, existing status/history consumers, and atomic rollback. Provision policy and safety data through a restricted operator workflow with audited approvals. Provision a slot clearance only when the underlying evidence covers the entire group for the full slot. Legacy reservations retain the original cancellation contract.

For rollback, disable gated operations first and preserve policy snapshots and reconciliation evidence. Use forward remediation after new lifecycle records exist unless an approved migration can restore service without altering financial history.

Finance acceptance should exercise the manual handoff as follows.

1. Open the exact platform merchant payment, verify the order, capture, currency and refundable amount against the Eco ledger, then approve the request.
2. Record processing when external processing is underway. Record reconciliation required when evidence conflicts, a request times out, or the outcome is uncertain. Resolve the existing attempt with the merchant before considering another submission.
3. Record success only after obtaining independent evidence of the completed refund, including the merchant refund reference and the exact refunded amount. Record failed only when the merchant confirms the refund failed. An ambiguous or intermediate merchant state stays in reconciliation or processing.
4. Verify that repeated submissions of the same evidence produce one history transition and that another Eco refund cannot claim the same merchant refund reference. Confirm that the shared transaction retains the original capture identity and that refund totals track the completed refund ledger.
