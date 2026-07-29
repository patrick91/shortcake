---
release type: patch
---

Fix the status column not tracking the highlighted option in the `sc submit`
scope menu: a branch moving into scope kept reading "not submitted", and one
leaving it kept promising "create PR".

The scope menu also no longer waits on GitHub before drawing. It looks up one
PR per branch, which left the terminal blank for seconds on a large stack; the
stack now appears immediately with each row marked while its own lookup is in
flight.
