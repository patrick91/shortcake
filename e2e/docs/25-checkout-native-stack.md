# Checking Out a Native GitHub Stack

`sc checkout <PR>` detects native GitHub membership and turns the whole stack
into local branches with Shortcake parent trailers. GitHub supplies the PR
order; Shortcake keeps the local commit metadata.

## Create a GitHub-only Stack

```console
$ # reset-to-main
$ # github: setup-mock-with-remote
$ git checkout -b native-a > /dev/null
$ echo "native a" > native_a.py && git add native_a.py
$ git commit -m "Native A" > /dev/null
$ git push -u origin native-a > /dev/null 2>&1
$ git checkout -b native-b > /dev/null
$ echo "native b" > native_b.py && git add native_b.py
$ git commit -m "Native B" > /dev/null
$ git push -u origin native-b > /dev/null 2>&1
$ # github: add-pr native-a 11 main
$ # github: add-pr native-b 12 native-a
$ # github: add-stack 11 12
$ git checkout main > /dev/null
$ git branch -D native-a native-b > /dev/null
```

## Check It Out

```console
$ sc checkout 12
Rebasing 'native-a' onto 'main'...
Rebasing 'native-b' onto 'native-a'...
Checked out PR #12 (native-b) with GitHub stack #1 (2 branches)
```

The requested PR branch is checked out and both trailers match GitHub's order:

```console
$ git branch --show-current
native-b
$ git log -1 --format=%B native-a | grep Shortcake-Parent
Shortcake-Parent: main
$ git log -1 --format=%B native-b | grep Shortcake-Parent
Shortcake-Parent: native-a
```

The native stack remains the remote representation:

```console
$ # github: show-stacks
stack #1: #11, #12
```
