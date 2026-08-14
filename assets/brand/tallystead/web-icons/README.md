# Tallystead web and PWA icons

These files are derived from the approved Tallystead icon. The browser SVG
responds to the user's light or dark color scheme. Installed-app icons use an
opaque navy field so they remain predictable across platforms.

## Browser icons

- `favicon.svg` — preferred modern browser favicon; light/dark adaptive.
- `favicon.ico` — fallback containing 16, 32, and 48 pixel images.
- `favicon-16x16.png`, `favicon-32x32.png`, and `favicon-48x48.png` — PNG fallbacks.
- `safari-pinned-tab.svg` — single-color Safari pinned-tab mask.

## Apple and PWA icons

- `apple-touch-icon.png` — 180 × 180 Apple home-screen icon.
- `pwa-192x192.png` — standard PWA icon.
- `pwa-512x512.png` — high-resolution standard PWA icon.
- `pwa-maskable-192x192.png` — maskable icon with protected safe area.
- `pwa-maskable-512x512.png` — high-resolution maskable icon.

Recommended manifest entries:

```json
{
  "icons": [
    {
      "src": "/icons/pwa-192x192.png",
      "sizes": "192x192",
      "type": "image/png",
      "purpose": "any"
    },
    {
      "src": "/icons/pwa-512x512.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "any"
    },
    {
      "src": "/icons/pwa-maskable-192x192.png",
      "sizes": "192x192",
      "type": "image/png",
      "purpose": "maskable"
    },
    {
      "src": "/icons/pwa-maskable-512x512.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "maskable"
    }
  ]
}
```

Recommended document metadata:

```html
<link rel="icon" href="/icons/favicon.svg" type="image/svg+xml">
<link rel="icon" href="/icons/favicon.ico" sizes="any">
<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">
<link rel="mask-icon" href="/icons/safari-pinned-tab.svg" color="#193A59">
```
