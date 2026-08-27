# Uumi evidence

Uumi stores canonical runtime evidence in the locked, versioned, CMEK-protected evidence bucket.
Objects are grouped by organisation, rotation run, and evidence type:

```text
organisations/<organisation-id>/runs/<run-id>/types/<type>/<evidence-id>
```

Sanitised submission exports belong under `evidence/runs/<run-id>/`. Each export has one
`manifest.json` and type directories such as:

```text
modelarmor/
registry/
sessions/
memory/
agents/
rotation/
browser/
audit/
```

Every manifest entry records its relative path, SHA-256 digest, content type, source resource,
and capture time. Exports must never contain credential values, access tokens, cookies, MFA data,
private keys, raw browser frames with secrets, hidden model reasoning, or unsanitised prompts and
responses. Model Armor evidence contains verdicts, filter states, and content hashes only.
