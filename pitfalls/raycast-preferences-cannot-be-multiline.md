# A multi-value preference can only hold one line

## Symptoms (grep this section)

```text
must be equal to one of the allowed values
```

…from `ray lint` when a preference uses `type: "textarea"`.

Or, with no error at all: a preference documented as "one entry per line"
accepts only the first entry, because the user cannot type a newline into it.

## Cause

**Raycast has no multi-line preference type.** The allowed set is exactly:

```text
appPicker | checkbox | dropdown | password | textfield | file | directory
```

`Form.TextArea` exists, but that is a *form* component inside a command — it has
nothing to do with `preferences[]` in the manifest. A `textfield` preference
renders as a single-line input, so a newline is not enterable.

Verified twice: the type union in `@raycast/api`'s `Preference` interface, and
empirically — setting `"type": "textarea"` on a preference makes `ray lint` fail
with `must be equal to one of the allowed values`.

## Why this is easy to miss

The parser works fine. Assertions over a `"a | h1\nb | h2"` fixture pass. The
docs read sensibly. Nothing fails — the feature simply cannot be *configured*
past one entry, and only a user with a second remote ever finds out.

Documenting a newline-separated format is untestable-by-assertion in the way
that matters: the constraint lives in the settings UI, not in the code.

## Fix

Pick a separator that survives a single line. This extension uses `;` between
connections and `|` between fields within one:

```text
lab | local_ubuntu | ~/.cargo/bin/pueue ; gpu | gpu.example.com ; nas | ts_nas
```

`connections.ts` splits on `/[\n;]/` — newlines are still accepted, because a
value set by other means may contain them, but the UI can only ever produce
semicolons.

Say *why* in the user-facing docs. A reader who doesn't know Raycast
preferences are single-line will assume the semicolon is an arbitrary taste
and reach for a newline.

## Related

The same "it parses, so it must work" gap produced
the connection line that vanished silently — a
malformed entry was dropped without complaint, which is indistinguishable from
the feature being broken. Both are failures of the *configuration* surface
rather than the code, and neither shows up in a build or a test.
