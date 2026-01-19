# Phase 5: GitHub Integration

Commands for interacting with GitHub PRs.

## Commands

### 15. `sc submit`

Push and create/update PRs.

```bash
sc submit            # Submit stack
sc submit --draft    # As drafts
```

**Flow:**
1. Push each branch in stack
2. Create PR if none exists
3. Update PR base if changed
4. Add stack info to PR description

**Implementation:**
- For each branch (bottom to top):
  - Push to origin (force push)
  - Check if PR exists for branch
  - If not, create PR with parent as base
  - If yes, update base if needed
  - Update PR body with stack visualization

**PR Description:**
```markdown
## Stack

- #125 ← **this PR**
- #124
- #123

---

[Original description here]
```

**Tests:**
- [ ] Submit single branch (create PR)
- [ ] Submit stack (multiple PRs)
- [ ] Submit with existing PRs (update)
- [ ] Submit --draft
- [ ] PR base updates correctly

---

### 16. `sc checkout` / `sc co`

Smart checkout - works for local and remote branches.

```bash
sc checkout feature-1      # Local branch
sc checkout feature-1      # Not local? Fetch from remote + adopt
sc checkout 123            # By PR number
sc checkout                # Interactive picker
sc co feature-1            # Alias
```

**Flow:**
1. Branch exists locally → checkout
2. Branch only on remote → fetch, adopt (with stack inference), checkout
3. PR number → resolve to branch name, then above

**Implementation:**
- Check if branch exists locally
- If yes, checkout (offer to adopt if not tracked)
- If no, fetch from origin
- If PR number, use GitHub API to get branch name
- Infer stack from remote (read trailers from fetched commits)
- Adopt entire stack locally

**Tests:**
- [ ] Checkout local tracked branch
- [ ] Checkout local untracked branch (offer adopt)
- [ ] Checkout from remote
- [ ] Checkout by PR number
- [ ] Interactive picker

---

## GitHub API

Will need a GitHub client for:
- Creating PRs
- Updating PRs (base, body)
- Getting PR by number
- Getting PR for branch

Options:
- `gh` CLI (shell out)
- `PyGithub` library
- Raw REST API with `httpx`

Decision: Start with `gh` CLI, migrate to library if needed.

---

## Checklist

- [ ] Implement `sc submit`
- [ ] Implement `sc checkout` / `sc co`
- [ ] GitHub PR creation
- [ ] GitHub PR updates
- [ ] Stack visualization in PR body
