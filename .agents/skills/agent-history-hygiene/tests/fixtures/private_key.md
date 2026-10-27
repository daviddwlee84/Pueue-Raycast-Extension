# private key fixture

Contains a fake PEM-format private key block. `redact_private_keys`
must replace the whole block with `[REDACTED:private-key]` — the same
label SpecStory >= 2.4.0 emits for this class. A bare
mention of `PRIVATE KEY` in prose (below) is intentionally left as-is —
the redactor scopes to key *headers*, matching what detect-private-key
greps for, so prose no longer triggers a non-converging redact loop.

The header/footer are `__SYNTHETIC_PEM_*__` placeholders rather than
literal `BEGIN ... PRIVATE KEY` text: detect-private-key greps that
substring and honours no allowlist marker, so a literal here would fail
`git commit` in every downstream repo that installs this skill. The
staging helpers expand the placeholders inside their throwaway repo, so
the bytes under test are still byte-identical to a real key block.

__SYNTHETIC_PEM_BEGIN__
MIIEogIBAAKCAQEAFAKE_KEY_MATERIAL_FOR_TESTING_ONLY_DO_NOT_USE
xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
__SYNTHETIC_PEM_END__

And a bare mention of PRIVATE KEY outside any block — the redactor
must leave this untouched.
