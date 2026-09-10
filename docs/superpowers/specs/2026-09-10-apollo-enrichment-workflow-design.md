# Apollo Selective Enrichment Workflow

## Goal

Backend-only workflow:

Search institutions
→ inspect lightweight results
→ selectively enrich promising prospects
→ review
→ approve
→ import to My Leads.

Typical searches include property managers, developers, agencies, hostels and similar institutions.

## Core rule

Apollo search must NOT automatically enrich every result.

Search is broad and lightweight. Enrichment is an explicit action on selected prospects so credits are not wasted.

An unenriched prospect cannot be imported to My Leads.

## Workflow

### 1. Search

Example:

Property managers in Nairobi

Backend performs:

- Apollo Organization Search
- Apollo People Search for decision-makers
- persistence of discovered companies and contacts

Search results stay available for later processing.

### 2. Discovery

A discovered prospect may contain:

- company name
- Apollo organization ID
- company LinkedIn
- website/domain when available
- possible decision-maker
- role/seniority
- person LinkedIn when available
- preliminary quality score

Missing firmographic/contact fields are acceptable at this stage.

### 3. Selective enrichment

Add:

POST /api/apollo/prospects/{prospect_id}/enrich

Only the selected prospect is enriched.

Enrichment performs:

- Apollo Organization Enrichment
- Apollo People Enrichment for the best decision-maker
- persistence of non-null enriched fields
- quality-score recalculation

Person fields include:

- first name
- last name
- full name
- title
- LinkedIn
- email
- phone only when deliberately supported

Company fields include:

- domain
- website
- LinkedIn
- employee count
- city
- country
- industry

Missing Apollo enrichment values must not erase existing values.

### 4. Review and import

Workflow:

discovered
→ enriched
→ pending_review
→ approved
→ imported

pending_review
→ rejected

Import is allowed only when the prospect:

1. has been successfully enriched
2. has review_status = approved

On import:

- create/reuse Lead
- source = apollo
- lead_type = agency
- status = new
- assigned_to = logged-in sales rep
- copy company/contact details
- set imported_lead_id
- mark prospect imported

Repeating import must return the same Lead and create no duplicate.

## Contact selection

Do not use an unordered database `.first()` when multiple decision-makers exist.

Use a deterministic best decision-maker based on existing role/seniority scoring.

## Credit protection

No automatic bulk enrichment.

For initial implementation:

- one prospect enriched per explicit request
- no phone webhook workflow
- no background enrichment
- no frontend bulk enrichment work

## Backend acceptance gate

Before frontend work resumes, prove with one real prospect:

Search
→ company persisted
→ decision-maker persisted
→ enrich
→ enriched company/contact verified in DB
→ review
→ approve
→ import
→ Lead verified
→ import again
→ same Lead ID and no duplicate.

## Out of scope

- frontend redesign
- bulk enrichment UI
- automatic enrichment
- phone webhook orchestration
- unrelated refactoring
- replacing the Leads architecture
