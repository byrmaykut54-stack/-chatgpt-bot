# Regression checks before release

Use Python 3.12 and a disposable PostgreSQL 16 database. The integration suite
truncates businesses and rate_limits. Never set TEST_DATABASE_URL to production.

```sh
python -m pip install -r requirements.txt -r requirements-test.txt
python -m playwright install --with-deps chromium --only-shell
TEST_DATABASE_URL=postgresql://postgres:test-only@localhost:5432/mexay_test RUN_BROWSER_TESTS=1 python -m unittest discover -v
```

NEXORA CI supplies PostgreSQL and enables browser tests. Both environment variables
must be set for release validation; a successful run with skipped tests is insufficient.

Coverage:
- HTTP password recovery, new/old password login, invalid/expired/weak inputs,
  all prior sessions revoked, email failure token cleanup and recovery rate limiting.
- PostgreSQL rollback after password/token updates, concurrent single-use token redemption,
  duplicate registration rollback, appointment status tenant isolation and staff permissions.
- Calendar owner-only operations and business selection from the session; OAuth scope/state,
  refresh tokens, POST/PATCH/DELETE lifecycle, empty DELETE response, 404 cleanup and API failures.
- Full frontend running in Chromium at desktop/mobile widths: appointment details, actions,
  reloads, creation, and failed status requests including network loss. API responses are fixtures;
  database-backed HTTP behavior is tested separately.

Before enabling Google Calendar for customers, complete one smoke test with an isolated
Google account granting only calendar.events: connect, create an appointment, update it,
repeat sync (one event), cancel it (event removed), and reconnect after revoked consent.
These tests never access a real Google account or send real email. Actual Google consent,
provider delivery, production configuration and multi-worker scheduling races remain separate
release checks.
