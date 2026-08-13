# FireKey engineering rules

- Build complete, production-shaped features against the contracts in `firekey.md`.
- Keep server code in Python and infrastructure in Terraform or YAML.
- Prefer single-word lowercase file and directory names. Use at most one hyphen when a separator is necessary.
- Keep provider-specific behaviour in connectors. Core code must remain provider-independent.
- Agents reason and plan through typed tools. They must not receive or mutate plaintext credentials.
- Add comments only for non-obvious security invariants, recovery constraints, or provider behaviour.
- Run the relevant tests, lint, type checks, and infrastructure validation before committing.
- Split substantial work into coherent commits and push verified commits.
- Use one-line commit subjects beginning with `feat:`, `fix:`, or `refactor:`. Do not add commit scopes.

