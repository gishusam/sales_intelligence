# Apollo Credit-Aware Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a resumable company-search queue that enriches internally selected contacts only within Apollo's verified live balance and automatically imports sales-ready contacts into My Leads.

**Architecture:** Extend the existing search-run association into a per-run queue, normalize Apollo's credit response into a conservative budget, and add a synchronous user-triggered orchestrator protected by an account-wide database lock. Keep legacy prospect review/import routes intact while the new search-run endpoints bypass them.

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL/SQLite-compatible models, Pydantic, pytest.

**Spec:** `docs/superpowers/specs/2026-09-12-apollo-credit-aware-enrichment-milestone.md`

## Global Constraints

- Work only on `feature/apollo-prospecting-backend`.
- Never call real paid Apollo enrichment endpoints during automated development or tests.
- Apollo's live team balance is the only source of truth; unverifiable balance blocks a batch.
- A sales-ready prospect has both email and phone and is imported idempotently into My Leads.
- Legacy endpoints remain available.
- No outreach, campaigns, background spending, deployment edits, or manual deployment.

---

### Task 1: Credit usage client and conservative budget

**Files:**
- Modify: `backend/app/services/apollo.py`
- Create: `backend/app/services/apollo_credits.py`
- Modify: `tests/test_apollo_client.py`
- Create: `tests/test_apollo_credits.py`

**Interfaces:**
- Produces: `ApolloClient.get_credit_usage() -> dict`
- Produces: `normalize_credit_budget(payload: dict) -> ApolloCreditBudget`
- Produces: `ApolloCreditBudget.can_enrich_contact(reserved_attempts: int = 0) -> bool`

- [ ] Add a client test that expects `POST /usage_stats/credit_usage_stats` with the API-key header; run it and confirm RED.
- [ ] Implement the client method; run the focused test and confirm GREEN.
- [ ] Add parameterized normalization tests for separate pools, unified pool, reset date, insufficient balances, and malformed responses; confirm RED.
- [ ] Implement immutable normalized budget types using a 1 lead + 8 direct-dial reservation for separate pools and 9 lead credits for unified pools; confirm GREEN.
- [ ] Run `tests/test_apollo_client.py tests/test_apollo_credits.py`.

### Task 2: Persisted resumable queue and migration

**Files:**
- Modify: `backend/app/models/apollo_search_run.py`
- Create: `backend/migrations/013_apollo_credit_aware_enrichment.sql`
- Modify: `backend/app/services/apollo_persistence.py`
- Modify: `tests/test_apollo_search_history_models.py`
- Create: `tests/test_apollo_credit_enrichment_migration.py`
- Modify: `tests/test_apollo_search_persistence.py`

**Interfaces:**
- Produces: queue fields `status`, `processed_at`, `last_error`, and `contact_id` on `ApolloSearchRunProspect`.
- Produces: run fields for queue counters, credit snapshot, reset date, and timestamps.
- Produces: `create_search_run(...)`, `attach_prospect_to_search_run(...)`, and duplicate-safe queue mutations.

- [ ] Add model and migration contract tests for the new queue/run fields and uniqueness; confirm RED.
- [ ] Implement model and SQL migration changes; confirm GREEN.
- [ ] Add persistence tests proving every found company is attached once and queued state survives; confirm RED.
- [ ] Implement minimal persistence helpers; confirm GREEN.
- [ ] Run the focused model, migration, and persistence tests.

### Task 3: Company-first search-run API

**Files:**
- Modify: `backend/app/schemas/apollo.py`
- Modify: `backend/app/routers/apollo.py`
- Modify: `tests/test_apollo_search_schema.py`
- Modify: `tests/test_apollo_prospect_search_route.py`
- Modify: `tests/test_apollo_search_persistence.py`

**Interfaces:**
- Changes: `POST /api/apollo/prospects/search` accepts company/location filters without requiring titles/seniorities and returns `search_run` progress while retaining `prospects` and `pagination` compatibility.
- Produces: `GET /api/apollo/search-runs/{run_id}` with counts, queue status, credit status, and reset date.

- [ ] Add schema and route tests proving company-first behavior and persisted run linkage; confirm RED.
- [ ] Create and attach the search run during discovery without invoking People Search; confirm GREEN.
- [ ] Add a run-detail contract test with hand-derived counters; confirm RED.
- [ ] Implement run serialization and the detail route; confirm GREEN.
- [ ] Run all search schema, route, and persistence tests.

### Task 4: Credit-aware enrichment orchestration and locking

**Files:**
- Create: `backend/app/services/apollo_queue.py`
- Modify: `backend/app/services/apollo_enrichment.py`
- Modify: `backend/app/routers/apollo.py`
- Create: `tests/test_apollo_credit_enrichment.py`
- Create: `tests/test_apollo_credit_enrichment_route.py`

**Interfaces:**
- Produces: `POST /api/apollo/search-runs/{run_id}/enrich` for both initial and continuation clicks.
- Produces: internal default title/seniority selection and per-company free People Search.
- Produces: account-wide non-blocking advisory lock on PostgreSQL, with process lock fallback for SQLite tests.

- [ ] Add tests proving unverifiable credit usage makes zero paid calls and preserves every queued item; confirm RED.
- [ ] Implement credit verification and stopped response; confirm GREEN.
- [ ] Add tests for internal contact selection, worst-case credit reservation, insufficient-credit stop, no-contact, failure, and resumable queue state; confirm RED one behavior at a time.
- [ ] Implement the minimum orchestration for each behavior, running each focused test GREEN before the next.
- [ ] Add and pass a concurrency-conflict route test.
- [ ] Run the full focused queue and route suite.

### Task 5: Automatic duplicate-safe My Leads import

**Files:**
- Modify: `backend/app/services/apollo_persistence.py`
- Modify: `backend/app/services/apollo_enrichment.py`
- Modify: `tests/test_apollo_lead_import.py`
- Modify: `tests/test_apollo_contact_webhook_route.py`
- Modify: `tests/test_apollo_my_leads_integration.py`

**Interfaces:**
- Changes: `import_prospect_to_my_leads` accepts the contact-ready primary flow without manual approval while preserving legacy approved import.
- Changes: webhook completion immediately imports with `source=apollo`, `status=new`, and existing assignment semantics.

- [ ] Add a failing service test for direct contact-ready import and repeated idempotent import.
- [ ] Implement the minimal import state transition and confirm GREEN.
- [ ] Add a failing webhook test proving email+phone completion auto-imports exactly one lead across repeated webhooks.
- [ ] Implement webhook auto-import using stored queue assignment context and confirm GREEN.
- [ ] Run all lead-import and webhook integration tests.

### Task 6: Regression, acceptance audit, and delivery

**Files:**
- Modify only files required by failures attributable to this milestone.

- [ ] Run `SECRET_KEY=test-secret JWT_SECRET_KEY=test-jwt-secret PYTHONPATH=.:backend pytest -q tests/test_apollo*.py` and fix milestone regressions through RED/GREEN cycles.
- [ ] Verify each milestone acceptance criterion against automated evidence; explicitly record that a real paid acceptance run and frontend work cannot be completed if no frontend source or credentials are present.
- [ ] Run `git diff --check` and `git status --short`.
- [ ] Commit all scoped changes on the current branch.
- [ ] Push the same branch and report the exact test result and final SHA.
