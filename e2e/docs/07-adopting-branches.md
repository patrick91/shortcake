# Adopting Existing Branches

The `sc adopt` command lets you track an existing branch that wasn't created with Shortcake.

## Setup: Create an Untracked Branch

Create a branch using regular git (not `sc create`):

```console
$ git checkout -b feature-login
$ echo "login code" > login.py && git add login.py
$ git commit -m "Add login functionality"
[feature-login 3be0939] Add login functionality
 1 file changed, 1 insertion(+)
 create mode 100644 login.py
```

This branch has no `Shortcake-Parent` trailer:

```console
$ git log -1 --format=%B | grep Shortcake-Parent || echo "No trailer found"
No trailer found
```

## Adopting the Branch

Use `sc adopt` to add tracking:

```console
$ sc adopt
Adopted 'feature-login' with parent 'main'
```

Now the branch has the parent trailer:

```console
$ git log -1 --format=%B | grep Shortcake-Parent
Shortcake-Parent: main
```

The branch appears in `sc ls`:

```console
$ sc ls
◉ feature-login (current)
│
◯ main
```

## Adopting with a Specific Parent

You can specify a different parent branch:

```console
$ git checkout main
$ git checkout -b feature-api
$ echo "api code" > api.py && git add api.py
$ git commit -m "Add API layer"
[feature-api d915f16] Add API layer
 1 file changed, 1 insertion(+)
 create mode 100644 api.py
$ sc adopt --parent feature-login
Adopted 'feature-api' with parent 'feature-login'
$ sc ls
◉ feature-api (current)
│
◯ feature-login
│
◯ main
```

## Adopting a Different Branch

You can adopt a branch without being on it:

```console
$ git checkout main
$ git checkout -b feature-utils
$ echo "utils" > utils.py && git add utils.py
$ git commit -m "Add utilities"
[feature-utils 339d8ab] Add utilities
 1 file changed, 1 insertion(+)
 create mode 100644 utils.py
$ git checkout main
$ sc adopt feature-utils
Adopted 'feature-utils' with parent 'main'
```
