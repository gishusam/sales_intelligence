# AGENTS.md

## Current priority

Before implementing Apollo prospecting work, read:

`docs/superpowers/specs/2026-09-12-apollo-credit-aware-enrichment-milestone.md`

That document is the source of truth for the current Apollo milestone.

## Repository workflow

Work on the branch supplied by the user. For the current milestone:

`feature/apollo-prospecting-backend`

Do not create another branch unless explicitly requested.

Read the existing implementation before changing behavior. Reuse working code instead of rewriting it unnecessarily.

## TDD requirement

Use strict TDD for backend behavior:

1. Add the smallest failing test.
2. Run it.
3. Confirm it fails for the expected reason.
4. Implement the minimum production change.
5. Run the focused test.
6. Run the relevant regression suite.

If RED fails unexpectedly, stop and investigate the root cause before changing production code.

Do not weaken or delete a test merely to make the suite green unless the product contract intentionally changed and the new expectation is documented.

## Apollo backend

Primary Apollo backend areas include:

- `backend/app/routers/apollo.py`
- `backend/app/services/apollo.py`
- `backend/app/services/apollo_enrichment.py`
- `backend/app/services/apollo_persistence.py`
- `backend/app/models/`
- `backend/app/schemas/apollo.py`
- `tests/test_apollo*.py`

Current product direction:

- Sales reps search companies/segments + location.
- Sales reps do not manually select decision-maker titles or seniority in the primary workflow.
- Contact selection happens internally.
- Search results must remain resumable.
- Bulk enrichment must use Apollo's verified live credit balance.
- Never guess available Apollo credits.
- If credit balance cannot be verified, bulk enrichment must not start.
- No automatic/background spending of future credits.
- A sales-ready prospect requires both email and phone.
- Contact-ready prospects are automatically imported into My Leads.
- Auto-import must remain duplicate-safe and idempotent.
- Manual review/approve/import is not part of the primary target workflow.
- Do not delete legacy endpoints until replacement behavior is proven and tests cover the new flow.

## Paid Apollo calls

Do not make real paid Apollo enrichment calls while implementing or running automated tests.

Use fakes/mocks/fixtures for unit and integration development.

A real Apollo acceptance run is performed only deliberately at the end of the milestone.

## Testing

From the repository root, run Apollo regression with:

```bash
SECRET_KEY=test-secret \
JWT_SECRET_KEY=test-jwt-secret \
PYTHONPATH=.:backend \
pytest -q tests/test_apollo*.py
```

Known baseline before this milestone:

```text
114 passed
1 existing passlib/crypt deprecation warning
```

Do not treat the existing passlib warning as a milestone regression.

## Completion checks

Before claiming a slice or milestone is finished:

```bash
git diff --check
git status --short
```

Run the complete Apollo regression suite and report the actual result.

For milestone completion, also verify the acceptance criteria in the milestone document one by one.

## Git and scope

Do not modify unrelated deployment files or deployment backup files.

Do not manually deploy.

Do not add outreach, campaigns, email sending, telephony, or unrelated CRM functionality.

Keep changes scoped to Apollo company discovery, credit-aware enrichment, queue/resume behavior, and automatic My Leads import.

When finished, report:

- files changed
- migrations added
- endpoints/contracts added or changed
- tests added or changed
- full test result
- remaining milestone items
- final commit SHA
