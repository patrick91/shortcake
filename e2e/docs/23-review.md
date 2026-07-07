# Review

The `sc review` command reviews a branch's changes using AI models (Claude, Codex).

## Setup

Create a tracked branch with a change to review:

```console
$ echo "def hello(): pass" > hello.py && git add hello.py
$ sc create -m "Add hello function"
Created branch 'add-hello-function' from 'main'
```

## No models available

When no AI CLI tools are installed, the command reports an error. To make this
reproducible we run `sc review` with a `PATH` that only contains `sc` and `git`,
so `claude`/`codex` are never found:

```console
$ mkdir -p .no-ai-bin && ln -sf "$(command -v sc)" .no-ai-bin/sc && ln -sf "$(command -v git)" .no-ai-bin/git
$ env PATH="$PWD/.no-ai-bin" sc review
Error: No AI review tools found. Install 'claude' or 'codex' CLI.
```
