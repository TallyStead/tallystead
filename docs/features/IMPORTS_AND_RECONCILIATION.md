# Imports and reconciliation

Tallystead Phase 4 adds a local CSV evidence pipeline without weakening ledger provenance.

- Reusable sources connect one CSV export procedure to one household account and retain only non-secret instructions.
- A user can select an example CSV, inspect its headers and first three rows locally, review Tallystead's suggested mappings, and save the approved mapping on the Source. Guesses never post ledger data.
- Source mappings support common header aliases, case-insensitive matching, common CSV delimiters, detected date formats, signed amounts with an explicit direction convention, or separate debit and credit columns.
- Optional original-description, status, category, and memo columns are retained as evidence. When an export contains cleaned and original descriptions, Tallystead uses the cleaned value for matching and preserves the original; Posted/Pending status carries into explicitly created transactions.
- Saved Sources can be edited for future imports. Once a Source has history, its account cannot change because that would reassign preserved evidence. Deleting an unused Source removes it; deleting a Source with history disables it while retaining its batches, raw rows, reconciliation evidence, and audit trail.
- File checksums make identical re-imports idempotent; row hashes expose repeats across or within batches.
- Candidate matches use account, direction, exact amount, three-day date tolerance, and payee agreement with an explanation and confidence.
- The review queue exposes unmatched, duplicate, invalid, deferred, and missing-expected-bill cases.
- Confirm, reject, defer, create-transaction, and unmatch actions are explicit and audited. Unmatching never deletes either side.
- Local reminder dates advance after successful imports when enabled.

The initial adapter does not silently infer a two-account transfer from a single account's CSV. Users may create or match the corresponding ledger evidence explicitly; future adapters can add explainable transfer pairing without changing this boundary.
