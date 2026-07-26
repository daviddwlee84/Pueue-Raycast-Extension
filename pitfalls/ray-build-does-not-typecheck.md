# `ray build` succeeds on code that `tsc` rejects

## Symptoms (grep this section)

```text
ready  - built extension successfully
```

…on a file with genuine type errors. Then, at runtime, a component receives the
wrong shape and either renders nothing or throws something unrelated-looking.

## Cause

`ray build` bundles with esbuild. esbuild **strips types without checking
them**. It reports success for anything syntactically valid.

Two real errors shipped past it in this repo:

1. The Groups view passed a `MutatePromise<GroupMap>` to an `act()` typed for
   `MutatePromise<State>`. At runtime its optimistic update would have replaced
   the group map with a task state — the list would have emptied on every
   action.
2. `@raycast/api` bundles **its own copy of `@types/react`**, so the root
   `React.ReactNode` is a structurally different type that silently fails to
   match `ActionPanel`'s children.

Neither produced a single line of output from `ray build`.

## Fix

```make
typecheck:
    npx tsc --noEmit -p tsconfig.json

check: typecheck verify lint build
```

`just check` is the gate. `ray build` alone proves only that the bundle exists.

## The React types clash specifically

When a prop needs to hold JSX, type it as an element rather than a node:

```ts
function logActions(task: Task, extra?: React.JSX.Element | null) { … }
```

`React.ReactNode` resolves to the *root* `@types/react`, which is not the copy
`@raycast/api`'s components are typed against.

## Note

`ray lint` doesn't typecheck either — it runs ESLint and Prettier. It does catch
things `tsc` won't, including reserved-shortcut collisions (`⌘K` and `⌘P` are
Raycast's own and are silently ignored if you bind them), so both are needed.
