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
