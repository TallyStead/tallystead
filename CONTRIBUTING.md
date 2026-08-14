# Contributing to Tallystead

Thank you for helping improve Tallystead. Contributions should preserve its local-first privacy model, deterministic financial behavior, and household security boundaries.

## Before opening an issue

- Search existing issues first.
- Use fictional or fully sanitized examples. Never attach real household exports, receipts, statements, credentials, tokens, backups, database contents, or private logs.
- Report suspected vulnerabilities privately according to [SECURITY.md](SECURITY.md), not through a public issue.
- Keep web-client issues in the `tallystead-web` repository when they do not require a server change.

## Development setup

Follow [README.md](README.md) for the local Docker stack. Install the API development dependencies before running its checks:

```sh
cd apps/api
python -m pip install '.[dev]'
ruff check .
pytest
```

Also validate the deployment and repository policies from the repository root:

```sh
docker compose --env-file infrastructure/compose/.env.example \
  --file infrastructure/compose/compose.yaml config --quiet
python3 scripts/check_brand_references.py
python3 scripts/check_repository_security.py
```

## Making a change

1. Create a focused branch from the current `main` branch.
2. Keep server authority, household scoping, integer-minor-unit money, provenance, and audit behavior intact.
3. Add or update tests for every changed behavior and failure boundary.
4. Update public documentation in the same pull request when behavior, configuration, deployment, security, or recovery changes.
5. Coordinate API changes used by `tallystead-web` and identify the compatible release order.
6. Run the relevant checks before opening a pull request.

Do not weaken authentication, authorization, migration, cryptography, import validation, network enforcement, backup verification, or CI security checks merely to make a test pass. A temporary exception must be documented, narrowly scoped, approved, and given an expiration date.

## Pull requests

Pull requests should explain:

- what changed and why;
- the user or operator impact;
- security, privacy, data-migration, and rollback considerations;
- tests and manual verification performed; and
- related server, web-client, issue, or documentation changes.

Keep changes reviewable and avoid unrelated formatting or dependency churn. Maintainers may ask for a change to be split when its security or release boundaries cannot be reviewed independently.

## License

By contributing, you agree that your contribution is licensed under this repository's [GNU Affero General Public License v3.0 only](LICENSE).

