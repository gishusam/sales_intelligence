from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database import Base, engine
from app.models import zone, lead, client,scraper_run # noqa: F401
from app.routers import leads
from app.routers import notes  
from app.routers import auth 
from app.routers import scraper
from app.routers import reports
from app.routers import email as email_router
from app.routers import communications as communications_router
from app.routers import communication_messages as communication_messages_router
from app.routers import communication_campaigns as communication_campaigns_router
from app.routers import communication_campaign_lifecycle as communication_campaign_lifecycle_router
from app.routers import communication_delivery_worker as communication_delivery_worker_router
from app.routers import communication_automation as communication_automation_router
from app.routers import communication_newsletters as communication_newsletters_router
from app.routers import communication_newsletter_delivery as communication_newsletter_delivery_router

Base.metadata.create_all(bind=engine, checkfirst=True)

app = FastAPI(
    title="Nyumba Zetu Sales Intelligence API",
    version="0.1.0",
    docs_url="/docs" if settings.DEBUG else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in settings.ALLOWED_ORIGINS.split(",")
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(leads.router)
app.include_router(notes.router)  
app.include_router(auth.router)
app.include_router(scraper.router)
app.include_router(reports.router)
app.include_router(email_router.router)
app.include_router(communications_router.router)
app.include_router(communication_messages_router.router)
app.include_router(communication_campaigns_router.router)
app.include_router(communication_campaign_lifecycle_router.router)
app.include_router(communication_delivery_worker_router.router)
app.include_router(communication_automation_router.router)
app.include_router(communication_newsletters_router.router)
app.include_router(communication_newsletter_delivery_router.router)



@app.get("/health")
def health_check():
    return {"status": "ok", "version": "0.1.0"}

@app.get("/debug/cors")
def debug_cors():
    return {
        "raw": settings.ALLOWED_ORIGINS,
        "parsed": [o.strip() for o in settings.ALLOWED_ORIGINS.split(",")]
    }