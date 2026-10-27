# sentinel-beside-live-key fixture

A partially redacted line: one inert `[REDACTED:<rule>]` sentinel AND one live
credential on the SAME line. Transcripts produce this whenever a tool echoes a
multi-value line and only some values were redactable.

The sentinel must not shield its line-mates. If the path-scoped allowlist ever
goes back to `regexTarget = "line"`, the live key below stops firing and this
fixture's test fails.

OPENAI_API_KEY=[REDACTED:openai-project-key] BACKUP_KEY=sk-proj-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa <!-- gitleaks:allow -->
