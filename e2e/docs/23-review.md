# Review

The `sc review` command reviews a branch's changes using AI models (Claude, Codex).

## Setup

```console
$ git init -b main test-review && cd test-review
Initialized empty Git repository in ...
$ git commit --allow-empty -m "Initial commit"
[main (root-commit) ...] Initial commit
$ git checkout -b feature
Switched to a new branch 'feature'
$ echo "def hello(): pass" > hello.py && git add hello.py
$ git commit -m "feat: add hello

Shortcake-Parent: main"
[feature ...] feat: add hello
```

## No models available

When no AI CLI tools are installed, the command reports an error.

```console
$ sc review
Error: No AI review tools found. Install 'claude' or 'codex' CLI.
```
