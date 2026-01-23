# Restacking After Changes

When you update a branch lower in the stack, use `sc restack` to update dependent branches.

## Setup: Create a Stack

```console
$ echo "CREATE TABLE users;" > schema.sql
$ git add schema.sql
$ sc create -m "Add database schema"
Created branch 'add-database-schema' from 'main'
$ echo "class UserRepo: pass" > repo.py
$ git add repo.py
$ sc create -m "Add repository layer"
Created branch 'add-repository-layer' from 'add-database-schema'
$ sc ls
◉ add-repository-layer (current)
│
◯ add-database-schema
│
◯ main
```

## Making Changes to Parent

Switch back to the parent branch and make changes:

```console
$ git checkout add-database-schema
$ echo "CREATE TABLE posts;" >> schema.sql
$ git add schema.sql
$ git commit -m "Add posts table to schema"
[add-database-schema 81bf7c8] Add posts table to schema
 1 file changed, 1 insertion(+)
```

## Checking What Needs Restacking

Use `--dry-run` to see what would be restacked:

```console
$ git checkout add-repository-layer
$ sc restack --dry-run
Would restack 1 branch(es):
  add-repository-layer onto add-database-schema
```

## Performing the Restack

Run `sc restack` to update the stack:

```console
$ sc restack
Rebasing 'add-repository-layer' onto 'add-database-schema'...
Restacked 1 branch(es) successfully.
```

Verify the child branch now includes the parent's changes:

```console
$ cat schema.sql
CREATE TABLE users;
CREATE TABLE posts;
```
