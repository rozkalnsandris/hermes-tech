# Hermes Tech search discovery and indexing evidence

This document separates repository-verifiable discovery controls from external
search-engine indexing state.

## Repository and generated-output contract

Hermes Tech uses the canonical public base URL:

```text
https://tech.rozkalns.net/
```

Hugo has `enableRobotsTXT = true`. The repository also carries an explicit
`site/layouts/robots.txt` template with this policy:

```text
User-agent: *
Allow: /

Sitemap: https://tech.rozkalns.net/sitemap.xml
```

Hugo generates `/sitemap.xml` for this monolingual site. CI must prove that the
rendered robots file advertises that sitemap, the sitemap is valid XML, sitemap
URLs remain under the canonical HTTPS origin, representative rendered pages use
self-consistent HTTPS canonical URLs, and no accidental `noindex` directive is
introduced on those pages.

The executable regression is `tests/test_search_discovery.py`.

## What this does not prove

A valid robots/sitemap/canonical configuration does **not** prove that Google or
another search engine has crawled or indexed a URL. A `site:` search returning
no results is also not authoritative indexing evidence.

Google Search Console is external account state and is not represented by this
repository. The following items therefore require read-only Search Console
evidence from the property owner:

- property verification for `tech.rozkalns.net` or the covering domain property;
- sitemap submission/discovery status and parser errors;
- Page Indexing status and exclusions;
- URL Inspection for the home page, each section, and a representative digest;
- crawl/host issues relevant to the property.

Sitemap submission is a discovery hint, not an indexing guarantee. Do not claim
that an indexing problem is fixed merely because the sitemap is generated or
submitted.

## Change boundary

Repository/generated-output failures are normal code or content issues and can
be fixed through the standard branch/PR/CI workflow. Search Console ownership,
submission and indexing state are manual/account-level evidence and should not
be guessed from repository code.

Do not request mass indexing, change canonical URLs, loosen robots policy, alter
Cloudflare behavior, or add search-engine-specific workarounds without evidence
of a concrete problem.

`HERMES_TECH_DEPLOY_REQUIRED=no` for this audit/documentation contract because it
does not change rendered site output.

`RPI5_MAIN_CHANGE_REQUIRED=no`
