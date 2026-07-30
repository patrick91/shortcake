---
release type: patch
---

Import commands when they run rather than at CLI startup. Every invocation used
to pay for every command, so `sc up` pulled in `httpx` and `yaml` through the
GitHub-touching commands it never calls.

Roughly halves startup for commands that do not talk to GitHub — `sc up` 227ms
to 104ms, `sc log` 201ms to 103ms — and helps every command to some degree.
`sc --help` is unchanged, since listing the commands still needs all of them.
