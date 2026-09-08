import httpx

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import NoResultFound

from app.config import settings
from app.auth import CurrentUser, get_current_user
from app.database import get_db
from app.models.apollo_prospect import ApolloProspect
from app.services.apollo import ApolloClient
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

            for raw_person in people_result.get("people", []):
                person = normalize_person(raw_person)
                organization_id = person["apollo_organization_id"]

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
        persist_discovered_prospect(
            db,
            prospect,
        )

    db.commit()

    return {
        "prospects": prospects,
        "pagination": result.get("pagination", {}),
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
