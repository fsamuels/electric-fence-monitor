from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import health

app = FastAPI(title="Fence Monitor API", version="0.0.1")

# Dev-only: frontend runs on the vite dev server (5173), API on 8000.
# Tightened to real deployment origins once one exists (see Security).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
