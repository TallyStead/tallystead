# Tallystead API contracts

## Authority and scope

FastAPI's generated OpenAPI 3.1 document is the canonical transport contract between a Tallystead server and independently released web or mobile clients. The contract describes paths, authentication, request and response shapes, and enumerations. It does not move financial calculations, authorization, household scoping, persistence, or other domain authority into a client.

All supported application routes use the `/v1` API namespace. The package version follows the compatible server release, while a future incompatible transport boundary requires an explicitly versioned API transition.

## Repository artifacts

`packages/contracts` contains:

- `openapi/tallystead-v1.json`, the deterministic platform-neutral contract;
- `src/generated/schema.ts`, generated TypeScript path and component types;
- a typed `openapi-fetch` client factory; and
- package metadata for `@tallystead/contracts`.

The OpenAPI and generated TypeScript files are derived artifacts. Change FastAPI routes or Pydantic schemas, then regenerate them; do not edit them by hand.

## Updating a contract

With the API development environment and Node.js 22 installed, run from the repository root:

```sh
python scripts/update_contracts.py
```

Windows PowerShell uses the same commands, with `python` or `py -3.12` according to the installed Python launcher.

The API change, OpenAPI snapshot, generated types, tests, and relevant documentation belong in the same pull request.

## Continuous enforcement

The Quality workflow:

1. regenerates OpenAPI from the application and rejects a stale snapshot;
2. regenerates TypeScript and rejects a stale generated client;
3. type-checks and builds the package; and
4. on pull requests, compares the candidate with the base branch and rejects common breaking changes, including removed operations, required inputs, removed response media types, removed schema properties, type changes, and removed enum values.

The compatibility check is intentionally conservative, but it does not replace review of semantic changes. A field whose type remains the same but whose financial meaning changes is still a contract change and must be documented and coordinated with every affected client.

## Releases and client compatibility

The manual server Release workflow publishes an immutable `@tallystead/contracts` version to GitHub Packages. It also attaches the OpenAPI document, package archive, CycloneDX SBOM, and checksum to the draft GitHub Release.

Clients pin a released contract version. `tallystead-web` may consume the generated TypeScript package directly. Future Swift and Kotlin repositories should generate native transports from the released OpenAPI document and retain the user-entered server URL as their connection origin.

Additive contract changes may remain in `/v1` when older clients continue to work. Removing or changing existing operations, required inputs, response shapes, enumeration values, authentication, or semantics requires a deliberate compatibility plan and coordinated release order.
