# `@tallystead/contracts`

This package contains the generated, versioned client/server contract for Tallystead. FastAPI's OpenAPI document is the canonical source. Do not hand-edit `openapi/tallystead-v1.json` or `src/generated/schema.ts`.

## Update the contract

From the server repository root, with the API development environment and Node.js 22 available:

```sh
python scripts/update_contracts.py
```

On Windows, use `python` or `py -3.12` according to the local Python installation. The commands are otherwise the same in PowerShell.

Commit API changes, the OpenAPI document, and generated TypeScript changes together. CI rejects contract drift and common breaking changes.

## Use from TypeScript

```ts
import { createTallysteadClient } from "@tallystead/contracts";

const client = createTallysteadClient({
  baseUrl: "https://tallystead.home.arpa",
  headers: { Authorization: `Bearer ${accessToken}` },
});

const { data, error } = await client.GET("/v1/accounts");
```

The caller supplies the user-selected server URL and authentication token. The package contains no hosted-service configuration, financial calculations, authorization logic, credentials, or household data.

## Other clients

The OpenAPI document is platform-neutral. Future Swift and Kotlin clients should generate their native transport layer from `openapi/tallystead-v1.json` or the same file attached to a Tallystead server release. Each client pins a released contract version compatible with its minimum supported server.
