import httpx
from secrets import compare_digest

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import NoResultFound

from app.config import settings
from app.auth import CurrentUser, get_current_user
from app.database import get_db
from app.models.apollo_prospect import (
    ApolloProspect,
    ApolloProspectContact,
)
from app.models.apollo_search_run import ApolloSearchRun
from app.services.apollo import ApolloClient
from app.services.apollo_enrichment import (
    apply_contact_details_webhook,
    enrich_prospect,
    request_contact_enrichment,
)
from app.schemas.apollo import ProspectSearchRequest
from app.services.apollo_normalizer import normalize_organization, normalize_person
from app.services.apollo_scoring import score_prospect
from app.services.apollo_queue import (
    ApolloEnrichmentAlreadyRunning,
    enrich_search_run,
)
from app.services.apollo_persistence import (
    attach_prospect_to_search_run,
    approve_prospect,
    create_search_run,
    import_prospect_to_my_leads,
    move_prospect_to_review_queue,
    persist_discovered_prospect,
    reject_prospect,
)


router = APIRouter(
    prefix="/api/apollo",
    tags=["apollo"],
)


@router.get("/health")
def get_apollo_health():
    configured = bool(settings.APOLLO_API_KEY.strip())

    if not configured:
        return {
            "configured": False,
            "connected": False,
        }

    client = ApolloClient(
        api_key=settings.APOLLO_API_KEY,
    )
    try:
        result = client.health()
    except httpx.HTTPError:
        return {
            "configured": True,
            "connected": False,
        }

    return {
        "configured": True,
        "connected": bool(result.get("is_logged_in")),
    }

@router.post("/prospects/search")
def search_prospects(
    request: ProspectSearchRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    client = ApolloClient(
        api_key=settings.APOLLO_API_KEY,
    )

    employee_ranges = [
        f"{request.employee_min},{request.employee_max}"
    ]

    use_people_first = bool(
        request.decision_maker_titles
        or request.decision_maker_seniorities
    )

    raw_people = []
    people_result = None

    if use_people_first:
        people_result = client.search_people(
            organization_ids=[],
            titles=request.decision_maker_titles,
            seniorities=(
                request.decision_maker_seniorities
            ),
            person_locations=request.locations,
            employee_ranges=employee_ranges,
            page=request.page,
            per_page=request.per_page,
        )

        raw_people = people_result.get(
            "people",
            [],
        )

        organization_ids = []
        seen_organization_ids = set()

        for raw_person in raw_people:
            organization_id = raw_person.get(
                "organization_id"
            )

            if not organization_id:
                organization_id = (
                    raw_person.get("organization") or {}
                ).get("id")

            if (
                organization_id
                and organization_id
                not in seen_organization_ids
            ):
                seen_organization_ids.add(
                    organization_id
                )
                organization_ids.append(
                    organization_id
                )

        if not organization_ids:
            return {
                "prospects": [],
                "pagination": people_result.get(
                    "pagination",
                    {},
                ),
            }

        result = client.search_organizations(
            locations=[],
            employee_ranges=employee_ranges,
            keywords=request.business_types,
            organization_ids=organization_ids,
            page=1,
            per_page=request.per_page,
        )
    else:
        result = client.search_organizations(
            locations=request.locations,
            employee_ranges=employee_ranges,
            keywords=request.business_types,
            page=request.page,
            per_page=request.per_page,
        )

    organizations = result.get(
        "organizations",
        [],
    )

    prospects = [
        normalize_organization(organization)
        for organization in organizations
    ]

    search_run = None
    if hasattr(db, "add"):
        search_run = create_search_run(
            db,
            filters=request.model_dump(),
            assigned_to=getattr(user, "name", "Apollo") or "Apollo",
        )

    if use_people_first:
        people_by_organization = {}

        organization_ids_by_name = {
            organization["name"].strip().casefold():
            organization["id"]
            for organization in organizations
            if organization.get("name")
            and organization.get("id")
        }

        valid_organization_ids = {
            organization.get("id")
            for organization in organizations
            if organization.get("id")
        }

        for raw_person in raw_people:
            person = normalize_person(
                raw_person
            )

            organization_id = person[
                "apollo_organization_id"
            ]

            if not organization_id:
                organization_id = (
                    raw_person.get("organization") or {}
                ).get("id")

            if not organization_id:
                organization_name = (
                    raw_person.get("organization") or {}
                ).get("name")

                if organization_name:
                    organization_id = (
                        organization_ids_by_name.get(
                            organization_name
                            .strip()
                            .casefold()
                        )
                    )

            if (
                not organization_id
                or organization_id
                not in valid_organization_ids
            ):
                continue

            person["apollo_organization_id"] = (
                organization_id
            )

            people_by_organization.setdefault(
                organization_id,
                [],
            ).append(person)

        for prospect in prospects:
            prospect["decision_makers"] = (
                people_by_organization.get(
                    prospect[
                        "apollo_organization_id"
                    ],
                    [],
                )
            )

    for prospect in prospects:
        prospect.update(
            score_prospect(prospect)
        )

        persisted = persist_discovered_prospect(
            db,
            prospect,
        )

        if persisted is not None:
            prospect["id"] = persisted.id
            prospect["review_status"] = (
                persisted.review_status
            )
            if search_run is not None:
                attach_prospect_to_search_run(
                    db,
                    search_run,
                    persisted,
                )

    db.commit()

    pagination = result.get(
        "pagination",
        {},
    )

    if people_result is not None:
        pagination = people_result.get(
            "pagination",
            {},
        )

    response = {
        "prospects": prospects,
        "pagination": pagination,
    }
    if search_run is not None:
        response["search_run"] = _serialize_search_run(search_run)
    return response


def _serialize_search_run(run: ApolloSearchRun) -> dict:
    return {
        "id": run.id,
        "status": run.status,
        "found_count": run.found_count or 0,
        "processed_count": run.processed_count or 0,
        "imported_count": run.imported_count or 0,
        "no_contact_count": run.no_contact_count or 0,
        "failed_count": run.failed_count or 0,
        "queued_count": run.queued_count or 0,
        "credit_status": run.credit_status,
        "billing_cycle_reset_at": run.billing_cycle_reset_at,
    }


@router.get("/search-runs/{run_id}")
def get_search_run(
    run_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    run = (
        db.query(ApolloSearchRun)
        .filter(ApolloSearchRun.id == run_id)
        .one_or_none()
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Apollo search run not found")

    result = _serialize_search_run(run)
    result["filters"] = run.filters
    return result


@router.post("/search-runs/{run_id}/enrich")
def enrich_contacts_for_search_run(
    run_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    webhook_secret = settings.APOLLO_WEBHOOK_SECRET.strip()
    if not webhook_secret:
        raise HTTPException(
            status_code=503,
            detail="Apollo webhook secret is not configured",
        )

    webhook_url = str(
        request.url_for(
            "receive_contact_enrichment_webhook"
        ).include_query_params(token=webhook_secret)
    )
    try:
        result = enrich_search_run(
            db,
            ApolloClient(api_key=settings.APOLLO_API_KEY),
            run_id,
            webhook_url=webhook_url,
        )
    except NoResultFound as exc:
        raise HTTPException(
            status_code=404,
            detail="Apollo search run not found",
        ) from exc
    except ApolloEnrichmentAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    db.commit()
    return _serialize_search_run(result.run)


@router.post("/webhooks/contact-enrichment")
def receive_contact_enrichment_webhook(
    payload: dict,
    token: str,
    db: Session = Depends(get_db),
):
    expected_secret = (
        settings.APOLLO_WEBHOOK_SECRET.strip()
    )

    if (
        not expected_secret
        or not compare_digest(
            token,
            expected_secret,
        )
    ):
        raise HTTPException(
            status_code=403,
            detail="Invalid Apollo webhook token",
        )

    updated = apply_contact_details_webhook(
        db,
        payload,
    )

    db.commit()

    return {
        "updated": updated,
    }


@router.post(
    "/prospects/{prospect_id}/contact-enrichment"
)
def start_contact_enrichment(
    prospect_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    webhook_secret = (
        settings.APOLLO_WEBHOOK_SECRET.strip()
    )

    if not webhook_secret:
        raise HTTPException(
            status_code=503,
            detail=(
                "Apollo webhook secret is not configured"
            ),
        )

    callback_url = request.url_for(
        "receive_contact_enrichment_webhook"
    )

    webhook_url = str(
        callback_url.include_query_params(
            token=webhook_secret,
        )
    )

    client = ApolloClient(
        api_key=settings.APOLLO_API_KEY,
    )

    try:
        contact = request_contact_enrichment(
            db,
            client,
            prospect_id,
            webhook_url=webhook_url,
        )
    except NoResultFound as exc:
        raise HTTPException(
            status_code=404,
            detail="Apollo prospect not found",
        ) from exc
    except httpx.HTTPError as exc:
        db.rollback()

        raise HTTPException(
            status_code=502,
            detail=(
                "Apollo contact enrichment request failed"
            ),
        ) from exc
    except ValueError as exc:
        db.rollback()

        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    db.commit()

    return {
        "id": prospect_id,
        "contact_id": contact.id,
        "contact_name": contact.name,
        "contact_enrichment_status": (
            contact.contact_enrichment_status
        ),
        "request_id": (
            contact.contact_enrichment_request_id
        ),
        "email": contact.email,
        "phone": contact.phone,
    }


@router.post("/prospects/{prospect_id}/enrich")
def enrich_selected_prospect(
    prospect_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    try:
        existing = (
            db.query(ApolloProspect)
            .filter(ApolloProspect.id == prospect_id)
            .one()
        )
    except NoResultFound as exc:
        raise HTTPException(
            status_code=404,
            detail="Apollo prospect not found",
        ) from exc

    if existing.review_status != "discovered":
        raise HTTPException(
            status_code=409,
            detail=(
                f"cannot enrich "
                f"{existing.review_status} prospect"
            ),
        )

    client = ApolloClient(
        api_key=settings.APOLLO_API_KEY,
    )

    try:
        prospect = enrich_prospect(
            db,
            client,
            prospect_id,
        )
    except httpx.HTTPError as exc:
        db.rollback()

        raise HTTPException(
            status_code=502,
            detail="Apollo organization enrichment failed",
        ) from exc
    except ValueError as exc:
        db.rollback()

        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    db.commit()

    return {
        "id": prospect.id,
        "review_status": prospect.review_status,
        "quality_score": prospect.quality_score,
        "quality_band": prospect.quality_band,
    }


def _serialize_prospect_summary(
    db: Session,
    prospect: ApolloProspect,
) -> dict:
    contact_ready = (
        db.query(ApolloProspectContact)
        .filter(
            ApolloProspectContact.prospect_id == prospect.id,
            ApolloProspectContact.email.isnot(None),
            ApolloProspectContact.phone.isnot(None),
        )
        .first()
        is not None
    )

    return {
        "id": prospect.id,
        "apollo_organization_id": (
            prospect.apollo_organization_id
        ),
        "name": prospect.name,
        "domain": prospect.domain,
        "website_url": prospect.website_url,
        "linkedin_url": prospect.linkedin_url,
        "employee_count": prospect.employee_count,
        "city": prospect.city,
        "country": prospect.country,
        "industry": prospect.industry,
        "quality_score": prospect.quality_score,
        "quality_band": prospect.quality_band,
        "review_status": prospect.review_status,
        "imported_lead_id": prospect.imported_lead_id,
        "contact_ready": contact_ready,
    }


def _serialize_contact(
    contact: ApolloProspectContact,
) -> dict:
    return {
        "id": contact.id,
        "apollo_person_id": contact.apollo_person_id,
        "first_name": contact.first_name,
        "last_name": contact.last_name,
        "name": contact.name,
        "title": contact.title,
        "seniority": contact.seniority,
        "linkedin_url": contact.linkedin_url,
        "email": contact.email,
        "phone": contact.phone,
        "enrichment_status": contact.enrichment_status,
        "contact_enrichment_status": (
            contact.contact_enrichment_status
        ),
        "contact_enrichment_request_id": (
            contact.contact_enrichment_request_id
        ),
    }


@router.get("/prospects")
def get_persisted_prospects(
    status: str | None = None,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    query = db.query(ApolloProspect)

    if status:
        query = query.filter(
            ApolloProspect.review_status == status
        )

    prospects = (
        query
        .order_by(ApolloProspect.id.desc())
        .all()
    )

    return {
        "prospects": [
            _serialize_prospect_summary(
                db,
                prospect,
            )
            for prospect in prospects
        ]
    }


@router.get("/prospects/review-queue")
def get_review_queue(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    prospects = (
        db.query(ApolloProspect)
        .filter(
            ApolloProspect.review_status == "pending_review"
        )
        .all()
    )

    return {
        "prospects": [
            {
                "id": prospect.id,
                "name": prospect.name,
                "domain": prospect.domain,
                "quality_score": prospect.quality_score,
                "quality_band": prospect.quality_band,
                "review_status": prospect.review_status,
            }
            for prospect in prospects
        ]
    }


@router.get("/prospects/{prospect_id}")
def get_persisted_prospect(
    prospect_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    prospect = (
        db.query(ApolloProspect)
        .filter(
            ApolloProspect.id == prospect_id
        )
        .one_or_none()
    )

    if prospect is None:
        raise HTTPException(
            status_code=404,
            detail="Apollo prospect not found",
        )

    contacts = (
        db.query(ApolloProspectContact)
        .filter(
            ApolloProspectContact.prospect_id
            == prospect.id
        )
        .order_by(ApolloProspectContact.id)
        .all()
    )

    result = _serialize_prospect_summary(
        db,
        prospect,
    )

    result["contacts"] = [
        _serialize_contact(contact)
        for contact in contacts
    ]

    return result


@router.post("/prospects/{prospect_id}/review")
def queue_prospect_for_review(
    prospect_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    try:
        prospect = move_prospect_to_review_queue(
            db,
            prospect_id,
        )
    except NoResultFound as exc:
        raise HTTPException(
            status_code=404,
            detail="Apollo prospect not found",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    db.commit()

    return {
        "id": prospect.id,
        "review_status": prospect.review_status,
    }


@router.post("/prospects/{prospect_id}/approve")
def approve_reviewed_prospect(
    prospect_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    try:
        prospect = approve_prospect(
            db,
            prospect_id,
        )
    except NoResultFound as exc:
        raise HTTPException(
            status_code=404,
            detail="Apollo prospect not found",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    db.commit()

    return {
        "id": prospect.id,
        "review_status": prospect.review_status,
    }


@router.post("/prospects/{prospect_id}/reject")
def reject_reviewed_prospect(
    prospect_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    try:
        prospect = reject_prospect(
            db,
            prospect_id,
        )
    except NoResultFound as exc:
        raise HTTPException(
            status_code=404,
            detail="Apollo prospect not found",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    db.commit()

    return {
        "id": prospect.id,
        "review_status": prospect.review_status,
    }


@router.post("/prospects/{prospect_id}/import")
def import_prospect_to_leads(
    prospect_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    try:
        lead = import_prospect_to_my_leads(
            db,
            prospect_id,
            assigned_to=user.name,
        )
    except NoResultFound as exc:
        raise HTTPException(
            status_code=404,
            detail="Apollo prospect not found",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    db.commit()

    return {
        "id": prospect_id,
        "lead_id": lead.id,
        "assigned_to": lead.assigned_to,
    }
