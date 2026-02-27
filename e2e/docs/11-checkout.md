# Checkout

The `sc checkout` command provides smart branch switching.

## Setup

Create some branches to checkout:

```console
$ echo "feature a" > a.txt && git add a.txt
$ sc create -m "Add feature A"
Created branch 'add-feature-a' from 'main'
$ sc down
Switched to 'main' (bottom of stack)
```

## Basic Checkout

Switch to an existing branch:

```console
$ sc checkout add-feature-a
Switched to 'add-feature-a'
```

## Using the co Alias

The `co` command is an alias for `checkout`:

```console
$ sc down
Switched to 'main' (bottom of stack)
$ sc co add-feature-a
Switched to 'add-feature-a'
```
