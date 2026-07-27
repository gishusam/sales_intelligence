# Cloud Run Database Pooling Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent Cloud Run API and scraper processes from exhausting the 15-client Supabase session pool and keep failed scraper runs from remaining stuck.

**Architecture:** Supabase transaction-mode pooling on port `6543` becomes the Cloud Run database boundary. The API uses SQLAlchemy `NullPool`, while worker status writes receive a short bounded connection retry that never retries scraper side effects.

**Tech Stack:** Python 3.11/3.12, FastAPI, SQLAlchemy 2.0, psycopg2, pytest, Cloud Run, Supabase Supavisor.

## Execution Result

Completed on 2026-07-27. Focused tests passed (`19 passed`), and the full suite
added no failures beyond the two pre-existing BuyRentKenya fixture failures
captured in the baseline (`2 failed, 32 passed` after the change). Candidate run
`4` and production run `5` both completed successfully with 12 auditable
records. Production now serves revision `sales-intelligence-api-00022-xux` and
uses the tested transaction-pool configuration.

## Global Constraints

- Preserve the current session-mode secret and previous Cloud Run revisions for rollback.
- Do not change SMTP, Groq, or other plaintext environment variables.
- Do not change database schema or Supabase compute size.
- Do not send production traffic to an unverified revision.
- Update `memory.md` with the verified behavior and rollback path.

---

### Task 1: Remove process-local API connection retention

**Files:**
- Create: `tests/test_database_pooling.py`
- Modify: `backend/app/database.py`

**Interfaces:**
- Consumes: a SQLAlchemy database URL.
- Produces: `build_engine(database_url: str) -> sqlalchemy.Engine`.

- [ ] **Step 1: Write the failing behavioral test**

```python
def test_api_engine_opens_a_fresh_dbapi_connection_for_each_checkout():
    engine = database.build_engine("sqlite://")
    physical_connects = 0

    @event.listens_for(engine, "connect")
    def count_connects(*_args):
        nonlocal physical_connects
        physical_connects += 1

    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))

    assert physical_connects == 2
```

- [ ] **Step 2: Run the test and verify RED**

Run: `python -m pytest tests/test_database_pooling.py -q`

Expected: FAIL because `build_engine` does not exist.

- [ ] **Step 3: Implement the minimal engine factory**

```python
from sqlalchemy.pool import NullPool

def build_engine(database_url: str):
    return create_engine(
        database_url,
        pool_pre_ping=True,
        poolclass=NullPool,
    )

engine = build_engine(get_database_url())
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_database_pooling.py -q`

Expected: one passing test and no warnings.

### Task 2: Preserve worker failure state during transient DB pressure

**Files:**
- Modify: `tests/test_worker.py`
- Modify: `github_agent.py`

**Interfaces:**
- Produces: `connect_database(max_attempts: int = 3, base_delay_seconds: float = 1.0)`.
- `update_run()` uses the helper; scraper subprocess execution remains unchanged.

- [ ] **Step 1: Write the failing retry test**

```python
def test_worker_retries_transient_database_failure_when_updating_run(monkeypatch):
    attempts = []
    connection = Connection()

    def connect(_url):
        attempts.append(1)
        if len(attempts) < 3:
            raise github_agent.psycopg2.OperationalError("max clients reached")
        return connection

    monkeypatch.setattr(github_agent.psycopg2, "connect", connect)
    monkeypatch.setattr(github_agent.time, "sleep", lambda _seconds: None)

    github_agent.update_run(7, {"status": "failed", "error": "scrape failed"})

    assert len(attempts) == 3
```

- [ ] **Step 2: Run the test and verify RED**

Run: `python -m pytest tests/test_worker.py::test_worker_retries_transient_database_failure_when_updating_run -q`

Expected: FAIL on the first `OperationalError`.

- [ ] **Step 3: Implement bounded connection retry**

Retry `psycopg2.OperationalError` up to three times with delays of one and two
seconds. Re-raise the third failure unchanged. Use the helper only in
`update_run`.

- [ ] **Step 4: Run focused worker tests and verify GREEN**

Run: `python -m pytest tests/test_worker.py -q`

Expected: all worker tests pass.

### Task 3: Verify candidate artifacts and transaction connection

**Files:**
- Modify: `memory.md`

**Interfaces:**
- Cloud Run secret: `sales-db-transaction-url`.
- Candidate API tag: `db-pool-candidate`.
- Candidate worker job: `sales-scraper-candidate`.

- [ ] **Step 1: Run full local tests**

Run: `python -m pytest -q`

Expected: no failures beyond the two pre-existing BuyRentKenya fixture failures
captured in the pre-change baseline.

- [ ] **Step 2: Verify transaction pooling read-only**

Derive a port-`6543` URL from the existing secret without printing either URL.
Connect with `application_name=pooling-preflight`, run `SELECT 1`, and close.

Expected: query returns `1`.

- [ ] **Step 3: Build API and worker images**

Build immutable candidate image tags and record their digests.

Expected: both Cloud Build operations exit `0`.

- [ ] **Step 4: Deploy isolated candidates**

Create `sales-db-transaction-url`, deploy the API image with zero traffic and
tag `db-pool-candidate`, and deploy `sales-scraper-candidate` with port `6543`.

Expected: candidate revision ready at 0% production traffic and candidate job
ready.

- [ ] **Step 5: Verify candidates**

Check tagged API `/health`, authenticated read endpoints, candidate job output,
database run state, and error logs.

Expected: API responses are correct, worker produces non-zero real output, run
state reaches a terminal status, and no database-capacity error appears.

### Task 4: Production rollout and user-visible proof

**Files:**
- Modify: `memory.md`

**Interfaces:**
- Production service: `sales-intelligence-api`.
- Production job: `sales-scraper`.

- [ ] **Step 1: Route API traffic to the tested revision**

Expected: the tested candidate revision receives 100% production traffic while
the previous revision remains available for rollback.

- [ ] **Step 2: Update the production worker**

Apply the tested worker image, transaction secret, and port `6543`.

Expected: job configuration matches the candidate digests and connection mode.

- [ ] **Step 3: Repair the historical stuck run**

Set run `1` to `failed` with its database-capacity error and completion time.

Expected: the run no longer appears `running`.

- [ ] **Step 4: Run a small production scrape through the API**

Start a bounded `agencies/Kilimani` scrape, poll to completion, and inspect
records.

Expected: the Cloud Run execution succeeds, the run reaches `success`, and
non-zero records are visible through the API/UI.

- [ ] **Step 5: Browser verification**

Use headless `agent-browser` to inspect the production dashboard and scraper
history. Capture proof of the terminal run and ensure the surrounding UI has no
obvious regression.

- [ ] **Step 6: Update memory and verify final state**

Record exact verification commands, tested image digests, rollout and rollback
details, and the explicit credential non-goal in `memory.md`. Re-run tests,
inspect logs, and verify Git/cloud state before reporting completion.
