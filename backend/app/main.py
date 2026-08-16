from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI

from app.agents.gemini import GeminiImpactInterpreter
from app.api.routes import router
from app.config import Settings, get_settings
from app.demo_data import build_demo_trip
from app.logging import configure_logging
from app.services.firestore import FirestoreIncidentRepository
from app.services.memory import InMemoryIncidentRepository, LocalEventPublisher
from app.services.ports import EventPublisher, IncidentRepository
from app.services.pubsub import GooglePubSubPublisher
from app.workflows.impact_analysis import ImpactAnalysisWorkflow


@dataclass
class AppContainer:
    settings: Settings
    repository: IncidentRepository
    publisher: EventPublisher
    workflow: ImpactAnalysisWorkflow


def build_container(settings: Settings) -> AppContainer:
    interpreter = GeminiImpactInterpreter(settings.gemini_model_id)
    if settings.pubsub_transport == "local":
        repository: IncidentRepository = InMemoryIncidentRepository()
        publisher: EventPublisher = LocalEventPublisher()
    else:
        repository = FirestoreIncidentRepository(settings.google_cloud_project)
        publisher = GooglePubSubPublisher(
            settings.google_cloud_project, settings.pubsub_topic_id
        )
    workflow = ImpactAnalysisWorkflow(
        repository,
        interpreter,
        lease_seconds=settings.event_lease_seconds,
    )
    return AppContainer(settings, repository, publisher, workflow)


def create_app(
    settings: Settings | None = None, *, container: AppContainer | None = None
) -> FastAPI:
    resolved_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configure_logging(resolved_settings.log_level)
        application.state.container = container or build_container(resolved_settings)
        if resolved_settings.seed_demo_data:
            await application.state.container.repository.seed_trip(build_demo_trip())
        yield

    application = FastAPI(
        title="Trip Recovery Agent",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.include_router(router)
    return application


app = create_app()
