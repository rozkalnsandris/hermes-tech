# Hermes Tech HTTP delivery policy

This document defines the application-owned HTTP expectations for the generated Hermes Tech site and the desired public-edge transport contract for `tech.rozkalns.net`. The host implementation belongs to `rozkalnsandris/RPi5_main`; Hermes Tech does not own the Nginx container lifecycle or the shared Cloudflare Tunnel.

The machine-readable source of truth is [`http-policy.json`](./http-policy.json).

## Cache policy

- Fingerprinted Hugo CSS (`/css/site.min.<hash>.css`): `Cache-Control: public, max-age=31536000, immutable`.
- HTML: `Cache-Control: no-cache` so browsers may retain a copy but must revalidate before reuse.
- RSS/XML and stable-name metadata such as `robots.txt`, `llms.txt`, and the web manifest: `Cache-Control: no-cache`.

Do not apply immutable caching to a stable URL merely because the current file contents rarely change.

## Origin security policy

The generated site is static HTML/CSS with no application JavaScript. CI therefore requires the site to remain compatible with a CSP that denies everything by default and allows only self-hosted images, styles, and the manifest. The policy explicitly denies scripts, objects, framing, forms, remote connections, media, and frames and does not use `unsafe-inline` or `unsafe-eval`.

Origin-owned required headers are:

- `Content-Security-Policy` from `http-policy.json`;
- `X-Content-Type-Options: nosniff`;
- `Referrer-Policy: strict-origin-when-cross-origin`;
- the minimal `Permissions-Policy` from `http-policy.json`.

`Strict-Transport-Security` is intentionally not origin-owned. The public site is Cloudflare-proxied, so the HSTS contract is edge-owned to keep the transport boundary scoped to the public hostname and to avoid duplicate header owners. Cloudflare's optional HSTS `No-Sniff` setting must remain disabled because the origin already owns `X-Content-Type-Options`.

## Cloudflare edge transport policy

The desired edge state is scoped only to `tech.rozkalns.net`. It must not use a zone-wide redirect or widen HSTS to sibling hosts.

For plain HTTP requests to `tech.rozkalns.net`:

- redirect to the same URL over HTTPS;
- use a permanent `301` response;
- preserve the original path;
- preserve the original query string;
- do not match unrelated hostnames.

For HTTPS responses from `tech.rozkalns.net`, emit exactly one:

`Strict-Transport-Security: max-age=15552000`

The initial HSTS contract is six months (`15552000` seconds), with `includeSubDomains` disabled and `preload` disabled. Any later increase in max-age, use of `includeSubDomains`, or preload requires a separate review because it expands rollback risk.

The machine-readable policy describes desired state, not evidence that the Cloudflare configuration is already active. Edge configuration changes remain a separate production authorization boundary.

## Ownership and verification

`RPi5_main` must source-control and test any Nginx configuration that emits the origin-owned headers. A Hermes Tech merge does not authorize a host service restart or a Cloudflare mutation.

Production acceptance for the edge transport contract requires separate public verification that:

1. `http://tech.rozkalns.net/<path>?<query>` returns a permanent redirect to the equivalent HTTPS URL;
2. HTTPS emits exactly one `Strict-Transport-Security` header with `max-age=15552000` and no `includeSubDomains` or `preload` directives;
3. the existing origin-owned CSP, `nosniff`, referrer, permissions, and cache headers remain unchanged;
4. unrelated `rozkalns.net` hostnames are unaffected;
5. the shared `cloudflared.service` lifecycle is unchanged.
