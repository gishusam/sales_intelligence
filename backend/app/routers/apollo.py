import httpx
from secrets import compare_digest

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import NoResultFound

from app.config import settings
from app.auth import CurrentUser, get_current_user
from app.database import get_db
from app.models.apollo_prospect import ApolloProspect
from app.services.apollo import ApolloClient
from app.services.apollo_enrichment import (
    apply_contact_details_webhook,
    enrich_prospect,
    request_contact_enrichment,
)
from app.schemas.apollo import ProspectSearchRequest
from app.services.apollo_normalizer import normalize_organization, normalize_person
from app.services.apollo_scoring import score_prospect
from app.services.apollo_persistence import (
    approve_prospect,
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

    result = client.search_organizations(
        locations=request.locations,
        employee_ranges=[
            f"{request.employee_min},{request.employee_max}"
        ],
        keywords=request.business_types,
        page=request.page,
        per_page=request.per_page,
    )

    organizations = result.get("organizations", [])

    prospects = [
        normalize_organization(organization)
        for organization in organizations
    ]

    if (
        request.decision_maker_titles
        or request.decision_maker_seniorities
    ):
        organization_ids = [
            organization.get("id")
            for organization in organizations
            if organization.get("id")
        ]

        if organization_ids:
            people_result = client.search_people(
                organization_ids=organization_ids,
                titles=request.decision_maker_titles,
                seniorities=request.decision_maker_seniorities,
                page=1,
                per_page=request.per_page,
            )

            people_by_organization = {}

            organization_ids_by_name = {
                organization["name"].strip().casefold():
                organization["id"]
                for organization in organizations
                if organization.get("name")
                and organization.get("id")
            }

            for raw_person in people_result.get("people", []):
                person = normalize_person(raw_person)
                organization_id = person["apollo_organization_id"]

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

                        if organization_id:
                            person["apollo_organization_id"] = (
                                organization_id
                            )

                if not organization_id:
                    continue

                people_by_organization.setdefault(
                    organization_id,
                    [],
                ).append(person)

            for prospect in prospects:
                prospect["decision_makers"] = (
                    people_by_organization.get(
                        prospect["apollo_organization_id"],
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
            prospect["review_status"] = persisted.review_status

    db.commit()

    return {
        "prospects": prospects,
        "pagination": result.get("pagination", {}),
    }



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
