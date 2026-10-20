# Security boundaries and limits

This portfolio uses fictional records. Keep real customer, supplier, payment and employee data out of it unless the surrounding deployment has the required organisational controls.

## Boundaries implemented in code

- Fixed supplier scope and separate read, execution and review permissions.
- Password sessions with a salted scrypt hash, expiry and same-origin action checks; an optional verified identity path.
- Server-only provider credentials and narrowly scoped tools.
- Input size, schema, decimal amount and currency validation.
- Saved source evidence, conditional storage writes and explicit request identifiers.
- Bounded model requests, visible failure states and separate human confirmation.
- A content security policy that authorises the exact shipped scripts.

The model cannot send email, authorise a payment, approve its own draft or rewrite source balances. The browser displays untrusted text through text nodes rather than treating it as markup.

## Deployment responsibilities

Use a private run bucket, Secret Manager and a service identity limited to the required resources. Supply distinct private service passes and a new private workspace password. Test fixture passwords and example tokens are public test data and must never become deployed credentials. The offline preview belongs on localhost.

The shared-password portfolio does not identify individual users. Recorded names are self-reported, and users with workspace access can see its saved correspondence. A company rollout needs individual sign-in, access policies, retention rules, logging and approved integrations.

Provider and cloud billing can lag. Request limits and alerts reduce exposure but do not guarantee a hard spending cutoff. Verify cloud access, durable storage and provider behaviour in the intended environment.

## Reporting a concern

Do not place credentials, passwords or private records in a public issue. A public issue can describe a concern without sensitive details; coordinate any sensitive reproduction material privately with the repository owner.

## Evaluation dependencies

The default development environment includes ADK evaluation tools. The container installs runtime dependencies only (`uv sync --frozen --no-dev`); it excludes the evaluation-only LiteLLM and NLTK packages.

The locked evaluation environment currently has two known advisory families:

- [LiteLLM proxy routing overrides](https://github.com/BerriAI/litellm/security/advisories/GHSA-3cv6-jpf6-8222). This application does not run a LiteLLM proxy or accept its routing parameters. The compatible evaluation dependency currently constrains LiteLLM below the patched release; do not expose a separate proxy from this environment.
- [NLTK model-artifact path controls](https://github.com/nltk/nltk/security/advisories/GHSA-8mgp-746c-j5xp). No patched release was listed at the 20 October 2026 check. This application does not expose the affected model import/export APIs. Keep evaluation input and model paths trusted.

These are documented dependency risks, not a claim of exploitability through the workspace. Keep evaluation tools out of production and recheck advisories before changing their use. A passing test suite or clean credential scan is not a security certification.
