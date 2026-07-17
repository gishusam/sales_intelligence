# Sales Intelligence Backend Memory

## Contents

- [Cloud deployment](#cloud-deployment)

## Cloud deployment

- The target architecture is a public FastAPI Cloud Run service plus an on-demand Cloud Run Job for Playwright scraping, both in GCP project `sales-intelligens` and region `europe-west1`.
- Supabase remains the managed PostgreSQL database. Runtime traffic must use the IPv4 Supavisor pooler with TLS; the direct `db.<project>.supabase.co` endpoint is IPv6-only in the current setup.
- The API should scale from zero with one maximum instance. The worker should execute one task at a time, use one vCPU and 1 GiB RAM, and stop after 15 minutes.
- Deployment work is being developed test-first on branch `cloud-run-deploy`. The Cloud Run request builder now emits one overridden task with dynamic run arguments and a 900-second timeout, matching the tested API contract.
- Job dispatch obtains the API service identity token from the Cloud Run metadata server and calls the Cloud Run v2 `jobs:run` endpoint. Dispatch failures mark the run failed instead of leaving it stuck in `running`; no GitHub token is part of this path.
- The worker now prefers one `DATABASE_URL` secret, while component-style PostgreSQL variables remain available to the existing scraper subprocesses. Railway/API credential removal remains the next test boundary.
- Worker completion now updates `scraper_runs` directly in PostgreSQL. The old Railway URL, hard-coded agent email/password, `requests` dependency, and authenticated HTTP callback have been removed from the worker execution path.
- The worker command builder no longer passes unsupported `--areas` arguments to the developer scraper; apartment and agency scrapers still receive the requested comma-separated areas.
- The worker reserves cleanup time inside the 15-minute Cloud Run limit by capping scraping at 12 minutes. Scraper or promotion subprocess failures now persist a failed run instead of reporting false success.
- `worker.Dockerfile` packages the Playwright runtime, scraper sources, promotion pipeline, and one-shot worker entrypoint. The root `.dockerignore` excludes `.env`, `.venv`, git metadata, tests, and local artifacts so secrets never enter Cloud Build context.
- `cloudbuild.worker.yaml` builds the worker with its non-default Dockerfile and publishes the image supplied through `_IMAGE`. Local `.env` now uses the IPv4 Supavisor session pooler with TLS; it remains git-ignored.
- Schema-contract tests now cover two stale boundaries found during deployment: listing promotion must target only current `leads` columns, and staging tables must contain the rating fields written by the Google Maps/developer scrapers.
- The pipeline listing promotion now matches the clean `leads` schema, and the canonical initialization adds the scraper rating fields. The same SQL enables RLS and removes `anon`/`authenticated` table and sequence grants because authorization belongs to FastAPI/JWT, not Supabase's Data API.
- The initial test import exposed an existing configuration defect: the default `ALLOWED_ORIGINS` value is a tuple because of a trailing comma. The deployment will fix that while retaining an explicit production origin allowlist.
- `ALLOWED_ORIGINS` is now a valid string default, and JWT signing fails closed through required `JWT_SECRET_KEY` settings rather than the repository's old insecure fallback value.
- Never commit database credentials, application secrets, or JWT keys. Store deployed values in GCP Secret Manager and keep local `.env` ignored.
- Cloud Run reserves `CLOUD_RUN_JOB`, so the API uses the application-specific `SCRAPER_CLOUD_RUN_JOB` environment variable for the worker job name.
- Deployment completed in GCP project `sales-intelligens`: the public API is `https://sales-intelligence-api-693887939589.europe-west1.run.app`, and the `sales-scraper` Cloud Run Job completed its smoke execution successfully in 50.49 seconds.
- A `$20` monthly project-scoped budget alert is configured. This is an alert, not a hard cap; the API is constrained to zero minimum and one maximum instance, while the worker is a single 15-minute task.
- The initial admin is `indrakenyadevelopment@gmail.com`; its temporary password is stored as `sales-admin-password` in GCP Secret Manager and must be changed on first login.
- Supabase is reachable through its TLS pooler. The canonical schema was applied idempotently, RLS is enabled, and direct `anon`/`authenticated` table and sequence grants are removed because access is mediated by the API.
- The July 15 clean-schema migration omitted `leads.notes` even though lead list, activity, timeline, detail, and update routes still use it. The canonical schema and SQLAlchemy model now include `notes TEXT`, and the additive migration was applied to Supabase.
- Cloud Run Job task retries are set to `1`. The scraper staging and promotion paths use unique constraints and `ON CONFLICT`, so one retry handles transient browser failures without intentionally duplicating leads.
- Sam (`samwelngugi24@gmail.com`) has Project Editor access. External Gmail Project Owner access must be invited from the Google Cloud Console and accepted by Sam; Google does not allow that owner invitation through `gcloud`.
- API revision `sales-intelligence-api-00002-6r5` uses image digest `sha256:2705e0226ac4e7f8480cdff2fbb328142273746aa0d6b5a3eb7669c27ccca59f`. The authenticated API-to-Job smoke run completed successfully, and the live read-endpoint sweep has no unexpected 500 responses.

## Maintenance log

- 2026-07-17: Created this memory file for the initial Cloud Run deployment and Supabase security review.
- 2026-07-17: Completed Cloud Run API and worker deployment, verified API login, and completed a real worker smoke execution against Supabase.
- 2026-07-17: Restored the missing `leads.notes` schema contract, redeployed the API, enabled one worker retry, added Sam as Project Editor, and verified API-to-worker execution end to end.
