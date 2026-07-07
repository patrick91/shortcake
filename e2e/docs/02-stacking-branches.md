# Stacking Branches

Shortcake lets you build stacks of dependent branches.

## Creating a Stack

First, create a base feature branch:

```console
$ echo "class User: pass" > user_model.py
$ git add user_model.py
$ sc create -m "Add user model"
Created branch 'add-user-model' from 'main'
```

Now create a second branch that depends on the first:

```console
$ echo "def get_user(): pass" > user_api.py
$ git add user_api.py
$ sc create -m "Add user API"
Created branch 'add-user-api' from 'add-user-model'
```

Check the stack structure:

```console
$ sc ls
◉ add-user-api (current)
│ Add user API
│
◯ add-user-model
│ Add user model
│
◯ main
  Initial commit
```

## Adding Another Branch to the Stack

You can continue building on the stack:

```console
$ echo "def validate(): pass" > validation.py
$ git add validation.py
$ sc create -m "Add validation"
Created branch 'add-validation' from 'add-user-api'
$ sc ls
◉ add-validation (current)
│ Add validation
│
◯ add-user-api
│ Add user API
│
◯ add-user-model
│ Add user model
│
◯ main
  Initial commit
```

## Verifying Parent Relationships

Each branch tracks its parent in the commit message:

```console
$ git log -1 --format=%B add-user-api | grep Shortcake-Parent
Shortcake-Parent: add-user-model
```

```console
$ git log -1 --format=%B add-validation | grep Shortcake-Parent
Shortcake-Parent: add-user-api
```
