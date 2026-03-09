# Viewing Branch Logs

The `sc log` command shows commits on the current branch relative to its parent.

## Setup: Create a Branch with Multiple Commits

```console
$ echo "config v1" > config.py && git add config.py
$ sc create -m "Add config module"
Created branch 'add-config-module' from 'main'
$ echo "config v2" >> config.py && git add config.py
$ git commit -m "Update config settings"
[add-config-module a98ccd5] Update config settings
 1 file changed, 1 insertion(+)
$ echo "config v3" >> config.py && git add config.py
$ git commit -m "Add more config options"
[add-config-module c5a199b] Add more config options
 1 file changed, 1 insertion(+)
```

## Viewing the Log

Use `sc log` to see commits on the current branch:

```console
$ sc log
◉ add-config-module
│
● c5a199b Add more config options
│
● a98ccd5 Update config settings
│
● bbc3d12 Add config module
│
◯ main
```

The log shows:
- The current branch at the top
- All commits on this branch (newest first)
- The parent branch at the bottom

## Log for a Stack

Create a child branch and view its log:

```console
$ echo "advanced config" > advanced.py && git add advanced.py
$ sc create -m "Add advanced config"
Created branch 'add-advanced-config' from 'add-config-module'
$ echo "more advanced" >> advanced.py && git add advanced.py
$ git commit -m "Extend advanced config"
[add-advanced-config 47ba7e7] Extend advanced config
 1 file changed, 1 insertion(+)
$ sc log
◉ add-advanced-config
│
● 47ba7e7 Extend advanced config
│
● b925205 Add advanced config
│
◯ add-config-module
```

The log only shows commits between the current branch and its parent, not the entire stack.

## Log on Untracked Branch

When on an untracked branch, log shows commits relative to the default branch:

```console
$ git checkout main
$ git checkout -b untracked-feature
$ echo "untracked code" > untracked.py && git add untracked.py
$ git commit -m "Untracked commit"
[untracked-feature <HASH>] Untracked commit
 1 file changed, 1 insertion(+)
 create mode 100644 untracked.py
$ sc log
◉ untracked-feature
│
● <HASH> Untracked commit
```
