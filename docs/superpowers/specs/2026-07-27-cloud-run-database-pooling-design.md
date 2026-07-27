# Cloud Run Database Pooling Fix Design

## Problem

The `sales-scraper-vddbv` Cloud Run Job failed before scraping because the
Supabase session-mode pool on port `5432` had reached its 15-client limit.
Cloud Run retried once and failed again. The worker's attempt to persist the
failure also could not connect, so `scraper_runs.id = 1` remained `running`.

The API currently compounds this risk: Cloud Run starts two Uvicorn worker
processes, and each SQLAlchemy engine may retain 10 pooled connections and
open 20 overflow connections. That local capacity is larger than the shared
Supabase session pool.

## Selected design

1. Give Cloud Run a dedicated Supabase transaction-mode connection secret
   using port `6543`. Keep the current session-mode secret unchanged for
   rollback.
2. Configure the API SQLAlchemy engine with `NullPool`. Supavisor owns
   transaction pooling; each API checkout therefore returns its client
   connection instead of retaining it in each Uvicorn process.
3. Keep the worker's explicit `psycopg2` connections, which are scoped and
   closed by the worker process, but point both `DATABASE_URL` and
   `POSTGRES_PORT` at transaction mode.
4. Add bounded retry to worker status persistence for transient PostgreSQL
   connection-capacity failures. This does not retry scraper side effects; it
   only protects the UI run-state update.
5. Repair the already-stuck run only after the new connection path has passed
   pre-production checks.

## Alternatives rejected

- Increasing Supabase compute or pool size treats capacity as the primary
  problem while retaining a serverless/session-pooling mismatch.
- Merely reducing SQLAlchemy `pool_size` leaves two process-local pools and
  creates another tuning threshold that can be exhausted.
- Retrying the whole scrape risks repeated external work and duplicate writes;
  Cloud Run already provides one task retry.

## Verification and rollout

1. Add regression tests that demonstrate the API engine does not reuse a
   process-local DBAPI connection and that transient status-update connection
   failures are retried without rerunning scraper work.
2. Run focused and full Python tests.
3. Prove the transaction connection string with a read-only query.
4. Build both API and worker images.
5. Deploy a zero-traffic tagged API revision and a separately named candidate
   worker job. Verify startup, health, authenticated read APIs, candidate
   worker completion, database records, and logs before changing production
   traffic or the production job.
6. Route production traffic to the tested API revision, update the production
   worker job to the tested image/configuration, repair run `1`, then run a
   small real scrape through the production API and verify the resulting UI/API
   state.

## Rollback

- API: route traffic back to the previous revision.
- Worker: restore the previous image, `POSTGRES_PORT=5432`, and
  `DATABASE_URL=sales-db-url:latest`.
- The existing `sales-db-url` secret is not modified.

## Explicit non-goals

- No changes to SMTP, Groq, or other plaintext environment variables.
- No database schema changes.
- No increase to Supabase compute or connection limits.
- No frontend feature changes.
