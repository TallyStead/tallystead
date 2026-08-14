# Tallystead Brand and Compatibility

## Product identity

**Tallystead** is the public and user-facing product name. The approved tagline is **“Your household finances, under your roof.”** Product interfaces, emails, reports, documentation, install metadata, and accessibility labels use Tallystead.

The public marketing website, domain redirects, hosted showcase, and website social-preview publishing are deferred until after v1. They are not part of the application rebrand.

## Approved marks

Production masters live in `assets/brand/tallystead`. The independently maintained `tallystead-web` repository owns the browser-ready copies it serves.

- Use `tallystead-horizontal.svg` as the primary wordmark on light surfaces.
- Use `tallystead-horizontal-dark.svg` on navy or similarly dark surfaces.
- Use the icon-only mark when less than 160 px of horizontal space is available.
- Use the tagline lockup for onboarding, brand guidance, and other spacious product surfaces.
- Keep clear space around a mark equal to at least one ledger-line thickness.
- Do not add currency symbols, gradients, shadows, outlines, or unapproved colors.
- Decorative icon duplicates use empty alternative text. A standalone wordmark or lockup uses the accessible name “Tallystead.”

## Approved color tokens

| Token | Color | Purpose |
| --- | --- | --- |
| Deep navy | `#193A59` | Navigation, dark surfaces, primary house outline |
| Household green | `#237A61` | Primary actions, status, ledger/check mark |
| Soft mint | `#E1F3EC` | Selected and supportive surfaces |
| Warm off-white | `#F7F4EE` | Application background |
| Slate | `#536477` | Supporting copy |
| Dark teal | `#234D5A` | Dark-surface icon interior |
| Light slate | `#C8D4DC` | Supporting copy on dark surfaces |

## Compatibility boundary

The rebrand must not force a destructive data migration, change a server origin, disconnect an installed client, invalidate a passkey, or make an old backup unreadable. New code and fresh installations use Tallystead identifiers. The remaining legacy compatibility surface is intentionally narrow:

- the existing repository/filesystem directory may retain its historical name;
- an already-created PostgreSQL database, database user, or MinIO bucket may keep its stored name through the local `.env`; fresh installations use Tallystead defaults;
- released web clients preserve the documented one-time migration from the two former browser connection/session keys to `tallystead.*`;
- archive import accepts the former `nestledger-household-archive-v1` manifest while new exports use `tallystead-household-archive-v1` and `.tallystead.zip`;
- existing canonical server URLs, audit records, and historical migration identifiers are never cosmetically rewritten.

Environment variables, package names, health metadata, protocol headers, Caddy paths, generated backup names, demo identifiers, and new archive/export formats use Tallystead naming.

Existing canonical server URLs are household-controlled identity values. Tallystead never silently rewrites a URL containing the former product name. Users may change a canonical URL only through the staged network workflow, with its passkey warning, readiness checks, and rollback protection.

## Contributor rule

Use **Tallystead** for all new product and implementation identifiers. Use a legacy identifier only for one of the compatibility cases above, and document any new exception. Run `scripts/check_brand_references.py` before committing rebrand-related work.
