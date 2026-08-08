# Security Policy

Hermes Tech is a self-hosted, openly AI-generated technology digest. Security reports are welcome, but potentially exploitable details and suspected secrets should not be posted in public issues.

## Supported version

Hermes Tech does not publish versioned application releases. Security support applies to the current `main` branch and the production deployment derived from it. Historical commits, abandoned branches, local forks, and modified deployments are not supported.

## Report a vulnerability privately

Use GitHub's **Security** area for this repository and choose **Report a vulnerability** when Private Vulnerability Reporting is available. Include enough information to reproduce and assess the issue without including unrelated personal data or secrets.

Useful reports normally include:

- the affected component and repository path;
- the observed and expected behavior;
- minimal reproduction steps or a proof of concept;
- the security impact and any prerequisites;
- relevant versions, commit SHAs, or sanitized logs;
- whether you believe credentials or other secrets may have been exposed.

If the private reporting button is unavailable, open a normal GitHub issue **without vulnerability details** and ask the maintainer for a private security contact. Do not paste proof-of-concept exploit details, tokens, passwords, private host addresses, backup contents, or other sensitive evidence into a public issue.

Please allow coordinated investigation and remediation before publishing exploit details.

## What belongs in a private security report

Use the private security channel for issues such as:

- vulnerabilities in the Python collection, AI-processing, validation, publication, or Hugo-serving code;
- authentication, authorization, path traversal, command execution, request-boundary, or injection flaws;
- unsafe handling of untrusted RSS/content input;
- suspected credential, token, secret, or private-backup exposure;
- dependency or build-chain findings with a concrete security impact;
- defects that could alter or publish content outside the intended fail-closed controls;
- host, Cloudflare, tunnel, backup, or deployment defects discovered through Hermes Tech when disclosure would expose infrastructure details.

Host-owned implementation is maintained separately in `rozkalnsandris/RPi5_main`. Do not disclose sensitive host evidence publicly merely because that repository is public; report it privately first and the maintainer will route it to the correct ownership boundary.

## What should use a normal public issue

Public GitHub issues are appropriate for non-sensitive problems such as:

- incorrect or stale article/content output with no security impact;
- broken links, metadata, layout, accessibility, or browser issues;
- documentation errors;
- feature requests and ordinary reliability bugs that do not reveal an exploitable condition or secret.

When unsure whether a report is security-sensitive, prefer the private channel.

## Disclosure and response

The maintainer will assess reports against the current repository and production boundary, request additional sanitized evidence when needed, and coordinate a fix before public disclosure when the report is valid. A security advisory may be used when appropriate.

This policy does not authorize testing that disrupts service, accesses data that is not yours, targets unrelated third-party systems or feed providers, degrades availability, or attempts to obtain secrets beyond what is necessary to demonstrate the issue safely.
