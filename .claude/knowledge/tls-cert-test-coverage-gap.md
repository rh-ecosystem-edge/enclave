---
title: TLS cert-handling test coverage gap — short-lived certs and CN/CNAME untested
tags: [tls, certificates, testing]
updated: 2026-07-16
---

PR #535 ("Harden TLS chain validation and CA resolution") added test coverage for three cert
variants — trusted-CA, complex-chain, and self-signed (`src/tests/test_system_ca.py`,
`test_check_certificate_chains.py`, `test_cert_utils.py`) — but two known variants remain completely
uncovered: short-lived/expiring certificates, and malformed CN/CNAME.

**Why:** `validations.sh` has expiry-check logic (`checkCACert`, ~line 118) with zero automated test
exercising it, and has no CN/CNAME validation logic at all.

**How to apply:** If picking up further TLS cert-handling test work, these two variants are the known
remaining gap — no need to re-derive which cases PR #535 already covers.
