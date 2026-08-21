---
release type: minor
---

Redesign the `sc ui` diff review screen around GitHub-style file cards.

Each file now renders as a rounded card on the page background, with a sticky
file header that keeps the card's rounded corners while scrolling and is
pushed off the top edge when its card ends. Diffs use GitHub's Primer colors
at full strength (backgrounds were previously diluted to ~10% by the diff
renderer's color mixing), the GitHub Primer syntax themes by default, and the
Monaspace Neon code font (bundled). File headers show the directory dimmed
with the filename bold, a status icon for added/deleted/renamed files, a
copy-path button, colored +/- counts with a diffstat meter, and a Viewed
toggle chip. The app header now shows the branch's commit subject and commit
count, and the theme/layout controls moved into a settings drawer that slides
in from the right without dimming the page, so theme changes are visible live.
