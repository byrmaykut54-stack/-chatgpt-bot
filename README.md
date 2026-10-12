# MexAy Business OS

Self-hosted appointment and customer management application, with tenant-specific accounts, roles, appointment status, usage limits and audit logs. The main application is `business_assistant.py`; the `agent/` folder is legacy unrelated code and is excluded from deployment images.

## Start

Use Python 3.12 and a durable PostgreSQL database. Install `requirements.txt`, configure `DATABASE_URL`, and run `python business_assistant.py`. Default port: 8080. Production must run behind HTTPS because session cookies are Secure. The Dockerfile supplies the same entry point.

For Render, `render.yaml` requests an existing database connection as a secret; it does not create a database. Confirm your database plan, retention and backups independently. The free web service may sleep and is not a production availability guarantee.

Set `PUBLIC_APP_URL` to your deployment's canonical HTTPS origin. Generate a Fernet key with `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` and store it as `NEXORA_ENCRYPTION_KEY` in deployment secrets. Keep the same key through redeployments and restore tests. New integration credentials cannot be stored without it. Back up the key separately and securely; losing it makes encrypted credentials unreadable.

Set `MEXAY_ADMIN_KEY` as a strong secret for the master administration panel, or set `NEXORA_ADMIN_EMAIL` for a registered business owner who may administer the system. Neither key belongs in client code or a sales ZIP.

## Optional integrations

| Feature | Required configuration | Buyer acceptance |
|---|---|---|
| Password recovery email | Resend `RESEND_API_KEY` + `RESEND_FROM_EMAIL`, or SMTP host/port/username/password/from; `PUBLIC_APP_URL` | Verify actual delivery, single-use reset and revoked old sessions |
| Google Calendar | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`; stable encryption key | Owner consent, create/update/cancel event and revoked-consent recovery |
| Gemini chat | `GEMINI_API_KEY`, optional `GEMINI_MODEL` | Validate provider access, cost and real responses |
| WhatsApp | Meta app/webhook and phone credentials; stable encryption key | Validate signature, tenant mapping and approved message flows |
| iyzico subscriptions | API key/secret, plan reference, callback URL and webhook configuration | Provider checkout, entitlement, cancel/refund and webhook acceptance |

Missing optional integrations must be listed as unavailable in the customer's handover. Provider fees, authorization, hosting and domains are separate costs. Core code delivery is not hosted SaaS subscription access.

## Acceptance before sale or handover

- Follow `TESTING.md` against a disposable PostgreSQL database; never use production for destructive tests.
- Check `/health` and `/api/health/db`; successful process health alone does not verify the database or optional providers.
- With a dedicated test account, verify signup/login, appointment creation/status/reload, account isolation, mobile navigation and logout.
- Confirm transactional email, backup/restore and each integration promised in the order on isolated provider accounts.
- Verify trial/plan administration for the agreed customer model. Do not advertise paid customer membership until checkout and access entitlement have been accepted.

See `SECURITY.md` and `WHATSAPP_SETUP.md`. No guaranteed income or continuous availability is promised by this repository.
