from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database import Base, engine
from app.models import zone, lead, client,scraper_run # noqa: F401
from app.routers import leads  
from app.routers import auth 
from app.routers import scraper

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
app.include_router(auth.router)
app.include_router(scraper.router)

@app.get("/health")
def health_check():
    return {"status": "ok", "version": "0.1.0"}

@app.get("/debug/cors")
def debug_cors():
    return {
        "raw": settings.ALLOWED_ORIGINS,
        "parsed": [o.strip() for o in settings.ALLOWED_ORIGINS.split(",")]
    }