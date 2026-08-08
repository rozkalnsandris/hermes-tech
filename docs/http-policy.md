# Hermes Tech HTTP delivery policy

This document defines the application-owned HTTP expectations for the generated Hermes Tech site. The host implementation belongs to `rozkalnsandris/RPi5_main`; Hermes Tech does not own the Nginx container lifecycle or the shared Cloudflare Tunnel.

The machine-readable source of truth is [`http-policy.json`](./http-policy.json).

## Cache policy

- Fingerprinted Hugo CSS (`/css/site.min.<hash>.css`): `Cache-Control: public, max-age=31536000, immutable`.
- HTML: `Cache-Control: no-cache` so browsers may retain a copy but must revalidate before reuse.
- RSS/XML and stable-name metadata such as `robots.txt`, `llms.txt`, and the web manifest: `Cache-Control: no-cache`.

Do not apply immutable caching to a stable URL merely because the current file contents rarely change.

## Security policy

The generated site is static HTML/CSS with no application JavaScript. CI therefore requires the site to remain compatible with a CSP that denies everything by default and allows only self-hosted images, styles, and the manifest. The policy explicitly denies scripts, objects, framing, forms, remote connections, media, and frames and does not use `unsafe-inline` or `unsafe-eval`.

Required headers are:

- `Content-Security-Policy` from `http-policy.json`;
- `X-Content-Type-Options: nosniff`;
- `Referrer-Policy: strict-origin-when-cross-origin`;
- the minimal `Permissions-Policy` from `http-policy.json`.

## Ownership and verification

`RPi5_main` must source-control and test any Nginx configuration that emits these headers. A Hermes Tech merge does not authorize a host service restart. Production acceptance requires separate loopback and public-edge verification and must confirm that the shared `cloudflared.service` lifecycle is unchanged.
