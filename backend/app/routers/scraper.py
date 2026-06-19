from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
import subprocess
from sqlalchemy import text
from datetime import datetime
from app.services.promote_leads import promote_apartments

from app.database import get_db
from app.models.scraper_run import ScraperRun

router = APIRouter(prefix="/api/scraper", tags=["Scraper"])


class ScrapeRequest(BaseModel):
    areas: list[str]
    scraper_type: str

@router.post("/run")
def run_scraper(
    payload: ScrapeRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):

    script_map = {
        "agencies": "../scraper/spiders/googlemaps.py",
        "apartments": "../scraper/spiders/apartments.py",
        "developers": "../scraper/spiders/developers.py",
    }

    script = script_map.get(payload.scraper_type)

    if not script:
        return {
            "success": False,
            "message": "Invalid scraper type"
        }

    area_string = ",".join(payload.areas)

    run = ScraperRun(
        source=payload.scraper_type,
        area=area_string,
        status="running"
    )

    db.add(run)
    db.commit()
    db.refresh(run)

    def start():

        try:

            subprocess.run(
                [
                    "python",
                    script,
                    "--areas",
                    area_string,
                ]
            )

            # update run status
            from app.database import SessionLocal

            session = SessionLocal()

            db_run = session.query(ScraperRun).get(run.id)

            if db_run:
                
                stats = promote_apartments(session, area_string)
                total = session.execute(
                    text("""
                        select COUNT(*)
                        FROM apartment_staging
                        WHERE search_area = :area
                    """),
                    {"area":area_string}
                ).scalar()

                
                contacts = session.execute(
                    text("""
                        SELECT COUNT(*)
                        FROM apartment_staging
                        WHERE search_area = :area
                          AND contact_phone IS NOT NULL
                          AND contact_phone <> ''
                    """),
                    {"area": area_string}
                ).scalar()

                duplicates = session.execute(
                    text("""
                        SELECT COUNT(*) - COUNT(DISTINCT normalized_name)
                        FROM apartment_staging
                        WHERE search_area = :area
                    """),
                    {"area": area_string}
                ).scalar()
                
                db_run.records_found = total
                db_run.imported = total
                db_run.with_contacts = contacts
                db_run.status = "success"
                db_run.completed_at = datetime.utcnow()
                db_run.duplicates = max(duplicates, 0)

                db_run.imported = stats["imported"]
                db_run.updated = stats["updated"]
                db_run.duplicates = stats["duplicates"]
                db_run.rejected = stats["rejected"]

            session.commit()
            session.close()

        except Exception:

            session = SessionLocal()

            db_run = session.query(ScraperRun).get(run.id)

            if db_run:
                db_run.status = "failed"

            session.commit()
            session.close()

    background_tasks.add_task(start)

    return {
        "success": True,
        "run_id": run.id,
        "message": f"{payload.scraper_type} scraper started",
        "areas": payload.areas,
    }

@router.get("/runs")
def get_runs(
    db: Session = Depends(get_db)
):

    runs = (
        db.query(ScraperRun)
        .order_by(ScraperRun.started_at.desc())
        .all()
    )

    return runs

@router.get("/runs/{run_id}")
def get_run(
    run_id: int,
    db: Session = Depends(get_db)
):

    run = (
        db.query(ScraperRun)
        .filter(ScraperRun.id == run_id)
        .first()
    )

    if not run:
        return {
            "success": False,
            "message": "Run not found"
        }

    return run