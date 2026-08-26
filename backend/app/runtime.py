from __future__ import annotations

from fastapi import FastAPI

from app.config import Settings, get_settings
from app.edge import create_edge_app
from app.main import create_app


def create_runtime_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    if resolved.app_role == "edge":
        return create_edge_app(resolved)
    return create_app(resolved)


app = create_runtime_app()
