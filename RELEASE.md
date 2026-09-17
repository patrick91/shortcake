---
release type: patch
---

Reduce GitHub API traffic from `sc ui` by sharing a five-minute cache across tabs
and pausing polling in hidden tabs. Stop refreshes when GitHub rate-limits requests,
respect its retry timing, and preserve previously loaded PR and CI information.
