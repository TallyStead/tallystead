# Local Assistant and Categorization

## Authority boundary

Phase 5B has two separate suggestion-only capabilities. Categorization may propose transaction splits, and the local assistant may explain authorized financial facts. Neither model receives direct database access or financial write tools.

All ledger amounts are deterministically converted from stored minor units into display-ready currency strings before they are included in assistant context. The model is instructed to quote those values exactly, so internal cent values such as `297371` are never presented as a household-facing amount.

The current assistant context and prompt contract is `assistant-readonly-v2`.

Phase 6 adds the active household plan and its configured steps as an authorized, cited read source. The assistant can explain the plan using citation `P1`; it still has no create, edit, reorder, allocation, reserve, or completion tools.

The categorization contract is `category-suggestion-v1`. The assistant contract is `assistant-readonly-v1`. Provider, model, rule/prompt version, confidence or sources, review state, and timestamps are retained locally.

## Categorization pipeline

Tallystead considers only uncategorized, non-transfer, regular pending or posted transactions that are not voids or reversals. Evidence is applied in this order:

1. An active household rule created from a previously accepted decision.
2. Categorized history with the same linked merchant or normalized payee and matching money direction.
3. Category hints from an accepted extraction attached through a confirmed document match.
4. Optional Ollama or LM Studio suggestion when local AI is enabled and deterministic evidence has no answer.

Every suggestion includes its proposed exact split, evidence, confidence, provider, and version. Owner and Manager may accept, edit, reject, or batch-review suggestions. Contributor and Viewer may inspect them. Acceptance verifies category direction and exact split equality, records a transaction revision and audit event, then applies the reviewed split. Rejection changes no transaction. Reconciled transactions must first be explicitly unreconciled.

Learning is explicit. When enabled during acceptance of a single-category suggestion, Tallystead creates or updates a merchant rule, or a normalized-payee rule when no merchant is linked. Rules can be disabled, reassigned, or deleted. A model response by itself never creates a rule.

## Read-only assistant

The full Assistant page and bottom-right popup share the same local conversation state. The interface uses the open-source Vercel AI SDK React transport for message state, plain-text streaming, stop, and regeneration. No Vercel account or hosted service is used.

FastAPI authenticates each request and constructs an authorized context from the versioned Phase 5A reporting service and upcoming obligations. The context contains explicit dates, one currency, ownership scope, deterministic totals and breakdowns, contributing transactions, and source identifiers. Only that context and recent messages are sent to the configured local Ollama or LM Studio runtime.

The model is instructed to use supplied facts without inventing or recalculating totals and to cite identifiers such as `[S1]`. Conversations, messages, citations, provider, and model version stay in PostgreSQL. Conversations are private to the signed-in household member. Caddy disables response buffering so tokens reach web and future mobile clients promptly.

The assistant has no create, edit, delete, categorize, match, reconcile, transfer, payment, or planning tools. If Local AI is disabled, chat returns a clear unavailable state while reports, manual categorization, and every core workflow continue working.

## Local data flow

```text
Assistant page or popup
        │ authenticated text stream
        ▼
FastAPI read-only assistant
        ├── Phase 5A reporting rules
        ├── authorized transaction citations
        └── upcoming obligations
        │ household-local prompt
        ▼
Ollama or LM Studio
```

The server contract remains authenticated HTTP plus a streaming response. Future iOS and Android clients can use the same server URL, conversations, filters, and citations without embedding web-only financial rules.
