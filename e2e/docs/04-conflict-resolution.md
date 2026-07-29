# Handling Conflicts During Restack

When branches modify the same files, conflicts can occur during restack.

## Setup: Create a Stack with Overlapping Changes

Create a parent branch with a file, then a child that modifies it:

```console
$ echo "line 1" > data.txt && git add data.txt
$ sc create -m "Add data file"
Created branch 'add-data-file' from 'main'
$ echo "line 2" >> data.txt && git add data.txt
$ sc create -m "Extend data file"
Created branch 'extend-data-file' from 'add-data-file'
```

## Cause a Conflict

Go back to the parent and add a conflicting change:

```console
$ git checkout add-data-file
$ echo "parent line" >> data.txt
$ git add data.txt && git commit -m "Add parent line"
[add-data-file 1e538d8] Add parent line
 1 file changed, 1 insertion(+)
```

## Attempt to Restack

When we try to restack from the child, we'll encounter a conflict:

```console
$ git checkout extend-data-file
$ sc restack
Restacking 1 branch

  ✗ extend-data-file  conflict

Conflict while rebasing 'extend-data-file' onto 'add-data-file'.

Fix conflicts in the following files:
  data.txt

Then:
  1. Stage your changes:     git add <files>
  2. Continue the restack:   sc continue

Or abort with: sc abort
```

## Resolve and Continue

Fix the conflict by writing the desired content, then continue:

```console
$ printf "line 1\nparent line\nline 2\n" > data.txt
$ git add data.txt
$ sc continue
Continuing rebase...
Restack completed successfully.
```

Verify the resolved file has all changes:

```console
$ cat data.txt
line 1
parent line
line 2
```

## Alternative: Abort the Restack

If you want to discard changes instead, use `sc abort`. First recreate a conflict:

```console
$ git checkout add-data-file
$ echo "another change" >> data.txt
$ git add data.txt && git commit -m "Another change"
[add-data-file 1e051f8] Another change
 1 file changed, 1 insertion(+)
$ git checkout extend-data-file
$ sc restack
Restacking 1 branch

  ✗ extend-data-file  conflict

Conflict while rebasing 'extend-data-file' onto 'add-data-file'.

Fix conflicts in the following files:
  data.txt

Then:
  1. Stage your changes:     git add <files>
  2. Continue the restack:   sc continue

Or abort with: sc abort
```

Abort and restore the original state:

```console
$ sc abort
Aborting in-progress rebase...
Restack aborted. Restored original branch state.
$ git branch --show-current
extend-data-file
```

## Abort with No Restack in Progress

Running `sc abort` when there's no active restack produces an error:

```console
$ sc abort
Error: No restack in progress.
```

## Continue with No Restack in Progress

Running `sc continue` when there's no active restack produces an error:

```console
$ sc continue
Error: No restack in progress.
```
