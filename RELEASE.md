---
release type: patch
---

Make `sc submit` push and create or update PRs from the bottom of the stack
through the current diff, so every PR base exists on GitHub. Use
`sc submit --stack` to include upstack branches; interactive runs also offer
to expand a downstack submission to the full stack. Submit now prints a
downward stack graph with each live PR action before prompting or acting, and
dims branches that are not selected. Existing PR numbers use the same cyan,
underlined, clickable links as `sc ls`.
PR stack descriptions omit branches excluded from that submission instead of
listing them as `(no PR)`.
