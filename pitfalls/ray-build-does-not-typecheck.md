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

More precisely: this is true of the **`dev` environment, which is the default**.
`ray build -e dist` *does* typecheck — it shells out to
`tsc -p tsconfig.json --noEmit`. Measured on a scratch extension carrying one
`TS2345`:

```console
$ npx ray build          # -e dev, what npm run build and ray develop use
ready  - built extension successfully                        # exit 0

$ npx ray build -e dist
src/tasks.tsx(9,46): error TS2345: Argument of type 'string' is not
  assignable to parameter of type 'number'.
    Error: TypeScript check failed (Command failed with exit code 2:
    ./node_modules/.bin/tsc -p tsconfig.json --noEmit)      # exit 1
```

Which makes it worse rather than better: the build you run all day is the one
that does not check, and `npm run build` is bare `ray build`.

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

`just dist` (`ray build -e dist`) is a second, stronger check worth running
before publishing — but it is not a substitute for `typecheck`, because it is not
the build in the edit loop.

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
