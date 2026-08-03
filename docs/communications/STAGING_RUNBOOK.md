# Communications staging migration and smoke-test runbook

This runbook is for the Nyumba Zetu Communications backend. **Do not run against production.** Complete the staging validation and obtain explicit approval before any production migration, push, merge, or deployment.

## 1. Freeze and backup

1. Confirm the feature branch is clean and all backend tests pass.
2. Record the current staging application revision and database version.
3. Create and verify a restorable staging database backup.
4. Confirm the rollback owner and rollback decision window.
5. Confirm these staging secrets are configured:
   - `COMMUNICATIONS_WORKER_TOKEN`
   - `EMAIL_WEBHOOK_SECRET`
   - `NEWSLETTER_UNSUBSCRIBE_SECRET`
   - `PUBLIC_API_BASE_URL` using HTTPS
   - `COMMUNICATIONS_SMTP_MOCK=false`
   - SMTP provider credentials
   - `GEMINI_API_KEY` only when AI drafting will be exercised

## 2. Migration preflight

Before applying the migration, inspect the target and save the output:

```bash
export PYTHONPATH=backend
export COMMUNICATIONS_PREFLIGHT_DATABASE_URL='postgresql://...staging...'
python scripts/communications_migration_preflight.py \
  | tee communications-preflight-before.json
```

A nonzero result is expected before the first Communications migration
because objects are missing. Review the missing-object list against
`migrations/2026_08_communications.sql`. Investigate any unexpected existing
objects, incompatible column types, conflicting constraints, or legacy
tables before continuing.

## 3. Apply the migration

Apply `migrations/2026_08_communications.sql` to staging only through the
team's approved database-migration mechanism. Do not paste production
credentials into a shell transcript. Do not execute the migration from this
runbook against production.

After the migration, rerun migration preflight:

```bash
python scripts/communications_migration_preflight.py \
  | tee communications-preflight-after.json
```

The report must return `"ready": true`.

## 4. Application readiness

Start the staging backend with the staging configuration, then call:

```bash
curl -sS \
  -H "Authorization: Bearer <staging-user-token>" \
  https://<staging-host>/api/communications/readiness
```

Also verify the worker-token endpoint:

```bash
curl -sS \
  -H "X-Worker-Token: <staging-worker-token>" \
  https://<staging-host>/api/communications/internal/readiness
```

Both responses must report `"ready": true`.

## 5. Smoke test

Use staging-only recipients and a noncustomer mailbox.

1. Create an active sender identity.
2. Create and render a manual communication template.
3. Create a draft campaign and add one staging recipient.
4. Run campaign preflight and schedule it.
5. Invoke the delivery worker once and verify the persisted message.
6. Create a follow-up automation rule but leave it inactive.
7. Create a newsletter draft with an unsubscribe block.
8. Add a staging audience, send a test, approve, and schedule.
9. Invoke the worker and confirm recipient/message lifecycle updates.
10. Post a correctly signed synthetic delivery event.
11. Replay the same provider event and confirm idempotency.
12. Post synthetic hard-bounce and complaint events for test addresses and
    verify suppression.
13. Add one active news source, ingest two normalized test articles, and
    verify duplicate detection.
14. Generate an AI-assisted newsletter draft and confirm it remains a draft.
15. Review `/api/communications/overview` and the provider-event history.

Do not use real customer addresses during the smoke test.

## 6. Acceptance evidence

Capture and retain:

- Full backend test output
- Before/after migration preflight JSON
- Readiness endpoint responses
- Migration execution log
- Campaign and newsletter test message IDs
- Provider event IDs and idempotent replay result
- Screenshots or API responses for the Communications overview
- Any deviations and their approved resolution

## 7. Rollback

Rollback when the migration fails, readiness is false, data integrity is
uncertain, or a smoke test creates incorrect state.

1. Stop Communications workers and scheduled invocations.
2. Roll back the application revision.
3. Restore the verified staging backup when schema/data rollback is needed.
4. Re-run the old application's health checks.
5. Preserve failure logs, preflight output, and message/event identifiers.
6. Open a corrective change; do not patch production directly.

The SQL migration is additive, but rollback must still use the approved
backup/restore path because later writes may depend on newly created objects.
