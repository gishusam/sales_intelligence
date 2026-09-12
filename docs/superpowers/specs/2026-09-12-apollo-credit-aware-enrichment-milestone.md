# Milestone — Apollo Company Discovery → Credit-Aware Enrichment → Auto My Leads

**Date:** 2026-09-12  
**Repository:** `gishusam/sales_intelligence`  
**Branch:** `feature/apollo-prospecting-backend`  
**Baseline commit:** `d15e90f941f83c26f6a24de97c15a7008b3d29a4`  
**Baseline commit message:** `feat: finish Apollo prospect discovery backend`

## Goal

Turn Apollo prospecting into a sales-ready company discovery workflow.

A sales rep should be able to search a market segment and location, for example:

> Developers in Nairobi

The system should discover companies, persist them as one resumable search run, enrich usable contacts only within the verified Apollo credit balance, and automatically create each sales-ready prospect in **My Leads**.

The salesperson should not need to choose a CEO, manager, director, or other job title manually.

---

## Locked Product Decisions

1. **The sales user searches companies/segments, not people.**
   - Example: `developers + Nairobi`.
   - Person selection happens behind the scenes.

2. **Searches are persisted as resumable search runs.**
   - If 100 companies are found, all 100 remain attached to that search run.
   - Remaining prospects are not lost when credits run out.

3. **Enrichment is user-triggered.**
   - No automatic overnight enrichment.
   - No spending future credits in the background.
   - The rep clicks **Enrich Contacts** or **Continue Enrichment**.

4. **Apollo's live credit balance is the source of truth.**
   - No hard-coded “30 per day” assumption.
   - Credit budget is shared across the Apollo team/account.

5. **Bulk enrichment must stop safely.**
   - Before spending credits, read Apollo's current credit usage.
   - If the balance cannot be verified, do not guess and do not start the batch.

6. **A sales-ready prospect requires both email and phone.**

7. **Contact-ready prospects are automatically sent to My Leads.**
   - No manual review → approve → import step in the primary workflow.
   - Existing duplicate protection/idempotency remains mandatory.

---

## Apollo Credit Rules to Respect

Implementation must use Apollo's live response rather than assume one fixed credit model.

Current Apollo API documentation states:

- `POST /api/v1/usage_stats/credit_usage_stats`
  - 0 credits.
  - Returns team-wide `limit`, `consumed`, `left_over`, and billing-cycle dates.
- People Search
  - 0 credits.
  - Does not reveal email or phone.
- Organization Search
  - 1 credit per page.
  - Up to 100 organizations per page.
- Standard People Enrichment
  - 1–9 credits/person when credit-consuming data is returned.
  - 1 credit for demographics/email.
  - +8 credits when a mobile number is returned.
- Apollo may expose separate pools such as `lead_credit` and `direct_dial_credit`, or a unified pool.

Official documentation:

- https://docs.apollo.io/reference/view-credit-usage-stats
- https://docs.apollo.io/reference/people-api-search
- https://docs.apollo.io/reference/organization-search
- https://docs.apollo.io/reference/people-enrichment
- https://docs.apollo.io/docs/api-pricing

---

## What Already Exists — Keep and Reuse

- [x] Apollo API client foundation
- [x] Organization discovery filters
- [x] People Search support
- [x] Prospect/company persistence
- [x] Prospect-contact persistence
- [x] Search-run/search-history models
- [x] Search-run-to-prospect relationship
- [x] Prospect scoring
- [x] Person enrichment primitive
- [x] Native Apollo phone reveal
- [x] Phone webhook persistence
- [x] Email + phone contact-ready validation
- [x] Persisted prospect/detail reads
- [x] My Leads import
- [x] Duplicate protection
- [x] Real end-to-end proof of email + phone reaching My Leads
- [x] Apollo regression baseline reported green locally: **114 passed, 1 existing passlib warning**

---

## What Changes

### Current primary flow

```text
search
→ discovered
→ select/enrich person
→ enriched
→ contact enrichment
→ pending review
→ approve
→ import
```

### Target primary flow

```text
search run
→ companies discovered
→ queued
→ enrich suitable contact internally
→ email + phone ready
→ automatically import to My Leads
```

Hold/failure states:

```text
queued
├── waiting_for_credits
├── no_contact
├── failed
└── contact_ready → imported
```

---

## Build Next

- [ ] Add Apollo credit-usage client method.
- [ ] Normalize Apollo credit usage into an internal budget model.
- [ ] Handle separate credit pools vs unified credit pool.
- [ ] Surface billing-cycle reset date.
- [ ] Make company + location the primary sales search contract.
- [ ] Remove job-title/seniority selection from the normal sales UX.
- [ ] Persist each search as a resumable search run.
- [ ] Track per-search-run queue state and progress.
- [ ] Add account-wide enrichment concurrency protection.
- [ ] Add `Enrich Contacts` / `Continue Enrichment` orchestration.
- [ ] Select the best available contact internally.
- [ ] Enrich only while the verified Apollo balance safely allows it.
- [ ] Stop safely when credits are insufficient.
- [ ] Keep remaining prospects queued.
- [ ] Report:
  - found count
  - processed count
  - imported/contact-ready count
  - no-contact count
  - failed count
  - queued count
  - current credit status
  - billing-cycle reset date
- [ ] Automatically import each contact-ready prospect into My Leads.
- [ ] Keep auto-import idempotent across retries/webhook repeats/Continue clicks.
- [ ] Retire or bypass manual review/approve/import from the primary Apollo UX.
- [ ] Update frontend to the new search-run/queue workflow.

---

## Example Acceptance Scenario

### Monday

Sales searches:

```text
Business type: Developers
Location: Nairobi
```

System:

1. Creates one search run.
2. Discovers and persists matching developer companies.
3. Attaches those prospects to the search run.
4. Shows the number waiting for enrichment.

Sales rep clicks **Enrich Contacts**.

System:

1. Reads Apollo's live team credit balance.
2. Selects a suitable contact for the next queued company.
3. Performs enrichment only when the current budget permits it.
4. Attempts to obtain both email and phone.
5. If both are available, marks the prospect sales-ready.
6. Immediately creates the corresponding My Lead.
7. Continues until the verified budget no longer safely permits another attempt.
8. Stops without discarding the remaining queue.

Example result:

```text
Developers — Nairobi

Found:              100
Processed:             8
Imported to My Leads:  6
No usable contact:     2
Waiting:              92

Apollo credits:
Current billing-cycle balance shown here.

[ Continue Enrichment ]
```

If credits are exhausted:

```text
Apollo enrichment credits are currently insufficient.

92 prospects remain queued.
Nothing has been discarded.

Credits reset on: <Apollo billing-cycle reset date>
```

### After Credits Reset

The sales rep opens the same search run and clicks **Continue Enrichment**.

The system resumes from the queued prospects without:

- rerunning the original search,
- duplicating previous enrichment work,
- or creating duplicate My Leads.

---

## Automatic My Leads Contract

When a prospect reaches contact-ready state:

```text
company
+ usable contact name
+ email
+ phone
```

the backend automatically imports it to My Leads.

Expected My Leads behavior:

```text
source = apollo
status = new
assigned using existing My Leads assignment behavior
duplicate-safe
```

Manual review/approve/import is not required in the primary workflow.

---

## Out of Scope

- Outreach campaigns
- Email sending
- Dialer/telephony integration
- Automated overnight enrichment
- Per-salesperson Apollo quotas
- Required manual job-title selection
- Automatically spending credits after the user leaves the workflow

---

## Milestone Completion Gate

This milestone is complete only when:

1. Backend queue/budget behavior is covered by TDD.
2. Search runs persist and resume correctly.
3. Credit-aware enrichment stops safely.
4. Remaining prospects survive until later continuation.
5. Every contact-ready prospect is automatically imported into My Leads.
6. Repeated requests/webhooks do not create duplicate leads.
7. Frontend consumes the search-run/queue contract.
8. A real Apollo acceptance run demonstrates:
   - search,
   - enrichment,
   - safe credit handling,
   - queued remainder,
   - automatic My Leads import,
   - and resume behavior.
