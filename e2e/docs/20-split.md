# Split Branch

The split feature allows extracting selected hunks from a branch into a new branch. This is exposed via the `POST /api/split-hunks` endpoint in the UI server. The new branch can be placed **before** (as a parent) or **after** (as a child) the source branch.

## Setup: Create a Stack

```console
$ printf 'def hello():\n    return "hello"\n\ndef goodbye():\n    return "goodbye"\n' > app.py && git add app.py
$ sc create -m "Add app functions"
Created branch 'add-app-functions' from 'main'
```

```console
$ printf 'def util():\n    return "util"\n' > utils.py && git add utils.py
$ sc create -m "Add utils"
Created branch 'add-utils' from 'add-app-functions'
```

Verify the initial stack:

```console
$ sc ls
◉ add-utils (current)
│
◯ add-app-functions
│
◯ main
```

## Split Before (via API)

Split a hunk from `add-app-functions` into a new branch placed before it. We test this by calling the Python business logic directly:

```console
$ sc checkout add-app-functions
Switched to 'add-app-functions'
```

```console
$ python3 -c "from dulwich.repo import Repo; from shortcake import _git as git; from shortcake.commands.move_lines import HunkSelection, _split_hunks; import subprocess; repo = Repo('.'); patch = subprocess.run(['git', 'diff', '--no-color', '--find-renames', '--full-index', 'main...add-app-functions'], capture_output=True, text=True, check=True).stdout; sections = patch.split('diff --git '); file_patch = next('diff --git ' + s.rstrip() for s in sections[1:] if 'app.py' in s.split(chr(10))[0]); hunks = [HunkSelection(file_path='app.py', file_patch=file_patch, hunk_index=0)]; result = _split_hunks(repo, source_branch='add-app-functions', commit_message='feat: extract hello', placement='before', hunks=hunks, no_verify=True); print(f'New branch: {result.new_branch}'); print(f'Placement: {result.placement}')"
New branch: feat-extract-hello
Placement: before
```

Verify the new stack structure:

```console
$ sc ls
◯ add-utils
│
◉ add-app-functions (current)
│
◯ feat-extract-hello
│
◯ main
```

Verify trailers are correct:

```console
$ git log -1 --format=%B feat-extract-hello | grep Shortcake-Parent
Shortcake-Parent: main
```

```console
$ git log -1 --format=%B add-app-functions | grep Shortcake-Parent
Shortcake-Parent: feat-extract-hello
```
