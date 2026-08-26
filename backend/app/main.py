from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import FastAPI, Request

from app.agents.gemini import GeminiImpactInterpreter
from app.agents.google_search_watch import JudgeGoogleSearchWatch, PerTravelerGoogleSearchWatch
from app.agents.itinerary_extractor import ItineraryExtractor
from app.agents.judge_chat import VertexJudgeChat
from app.agents.judge_impact import JudgeImpactInterpreter
from app.agents.live_flight_watch import AmadeusFlightWatch, AutonomousWatchGrounder
from app.agents.router import PerTravelerGeminiRouter
from app.api.connections import calendar_worker_router, gmail_worker_router
from app.api.connections import router as connections_router
from app.api.connections import worker_router as worker_connections_router
from app.api.routes import router
from app.api.telegram import router as telegram_router
from app.config import Settings, get_settings
from app.demo_data import build_demo_trip
from app.logging import configure_logging
from app.providers.amadeus import AmadeusFlightStatusClient
from app.providers.calendar import HttpGoogleCalendarApi
from app.providers.duffel import DuffelFlightQuoteClient
from app.providers.gemini_byok import GoogleGeminiKeyValidator, GoogleSecretManagerStore
from app.providers.gmail import HttpGoogleGmailDraftApi, TravelerGoogleActionProvider
from app.providers.guarded_demo import JudgeOnlyDemoProvider
from app.providers.telegram import TelegramBotApiGateway
from app.services.action_executor import ActionProvider
from app.services.ai_connections import AiConnectionService
from app.services.approval_tokens import ApprovalTokenManager
from app.services.calendar_oauth import CalendarOAuthService, HttpGoogleCalendarOAuthClient
from app.services.firestore import FirestoreIncidentRepository
from app.services.gmail_oauth import GmailOAuthService, HttpGoogleGmailOAuthClient
from app.services.memory import InMemoryIncidentRepository, LocalEventPublisher
from app.services.onboarding import TelegramOnboardingService
from app.services.outbox import DurableOutboxDispatcher
from app.services.ports import (
    EventPublisher,
    IncidentRepository,
    SecretStore,
    TelegramGateway,
    WorkflowCommandPublisher,
)
from app.services.pubsub import GooglePubSubPublisher, GooglePubSubWorkflowCommandPublisher
from app.services.telegram_ai import TelegramAiConnectionService
from app.services.telegram_calendar import TelegramCalendarService
from app.services.telegram_conversation import TelegramConversationService
from app.services.telegram_demo import TelegramDemoService
from app.services.telegram_gmail import TelegramGmailService
from app.services.telegram_planning import (
    TelegramPlanningService,
    VertexTripPlanner,
)
from app.services.telegram_recovery import TelegramRecoveryService
from app.services.telegram_trips import TelegramTripService
from app.services.trip_watch_workflow import TripWatchWorkflow, WatchGrounder
from app.workflows.impact_analysis import ImpactAnalysisWorkflow
from app.workflows.recovery import RecoveryWorkflow


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class AppContainer:
    settings: Settings
    repository: IncidentRepository
    publisher: EventPublisher
    workflow: ImpactAnalysisWorkflow
    onboarding: TelegramOnboardingService | None = None
    telegram_gateway: TelegramGateway | None = None
    recovery: RecoveryWorkflow | None = None
    telegram_recovery: TelegramRecoveryService | None = None
    telegram_trips: TelegramTripService | None = None
    ai_connections: AiConnectionService | None = None
    telegram_ai: TelegramAiConnectionService | None = None
    telegram_demo: TelegramDemoService | None = None
    telegram_conversation: TelegramConversationService | None = None
    telegram_planning: TelegramPlanningService | None = None
    trip_watch: TripWatchWorkflow | None = None
    secret_store: SecretStore | None = None
    clock: Callable[[], datetime] = utc_now
    command_publisher: WorkflowCommandPublisher | None = None
    outbox_dispatcher: DurableOutboxDispatcher | None = None
    calendar_oauth: CalendarOAuthService | None = None
    telegram_calendar: TelegramCalendarService | None = None
    gmail_oauth: GmailOAuthService | None = None
    telegram_gmail: TelegramGmailService | None = None
    connection_secret_store: SecretStore | None = None


def build_container(settings: Settings) -> AppContainer:
    if settings.pubsub_transport == "local":
        repository: IncidentRepository = InMemoryIncidentRepository()
        publisher: EventPublisher = LocalEventPublisher()
        command_publisher: WorkflowCommandPublisher = publisher  # type: ignore[assignment]
    else:
        repository = FirestoreIncidentRepository(settings.google_cloud_project)
        publisher = GooglePubSubPublisher(settings.google_cloud_project, settings.pubsub_topic_id)
        command_publisher = GooglePubSubWorkflowCommandPublisher(
            settings.google_cloud_project, settings.pubsub_command_topic_id
        )
    secret_store = GoogleSecretManagerStore(
        settings.google_cloud_project, settings.byok_secret_resource_name
    )
    connection_secret_store: SecretStore | None = None
    if settings.oauth_refresh_tokens_secret_resource_name:
        connection_secret_store = GoogleSecretManagerStore(
            settings.google_cloud_project,
            settings.oauth_refresh_tokens_secret_resource_name,
        )
    system_interpreter = GeminiImpactInterpreter(settings.gemini_model_id)
    judge_interpreter = (
        JudgeImpactInterpreter(
            repository,
            project=settings.google_cloud_project,
            location=settings.google_cloud_location,
            model=settings.gemini_model_id,
            daily_limit=settings.judge_daily_vertex_calls,
            daily_user_limit=settings.judge_daily_vertex_calls_per_user,
        )
        if settings.enable_judge_mode
        else None
    )
    interpreter = PerTravelerGeminiRouter(
        repository,
        secret_store,
        system_interpreter,
        settings.gemini_model_id,
        judge_interpreter=judge_interpreter,
    )
    workflow = ImpactAnalysisWorkflow(
        repository,
        interpreter,
        lease_seconds=settings.event_lease_seconds,
    )
    return AppContainer(
        settings,
        repository,
        publisher,
        workflow,
        secret_store=secret_store,
        connection_secret_store=connection_secret_store,
        command_publisher=command_publisher,
        outbox_dispatcher=DurableOutboxDispatcher(repository, command_publisher),
    )


def create_app(
    settings: Settings | None = None, *, container: AppContainer | None = None
) -> FastAPI:
    resolved_settings = settings or get_settings()
    if resolved_settings.app_role == "edge":
        raise ValueError("APP_ROLE=edge must use app.runtime or app.edge")

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configure_logging(resolved_settings.log_level)
        application.state.container = container or build_container(resolved_settings)
        telegram_client: httpx.AsyncClient | None = None
        byok_client: httpx.AsyncClient | None = None
        calendar_client: httpx.AsyncClient | None = None
        calendar_api_client: httpx.AsyncClient | None = None
        gmail_client: httpx.AsyncClient | None = None
        gmail_api_client: httpx.AsyncClient | None = None
        amadeus_client: httpx.AsyncClient | None = None
        if application.state.container.onboarding is None:
            application.state.container.onboarding = TelegramOnboardingService(
                application.state.container.repository,
                calendar_enabled=resolved_settings.enable_calendar_connections,
            )
        calendar_client_secret = ""
        gmail_client_secret = ""
        if resolved_settings.enable_calendar_connections:
            if application.state.container.calendar_oauth is None:
                secret_store = (
                    application.state.container.connection_secret_store
                    or application.state.container.secret_store
                )
                if secret_store is None:
                    raise RuntimeError("Calendar requires a Secret Manager store")
                calendar_client_secret = await secret_store.access_secret(
                    resource_name=resolved_settings.calendar_client_secret_resource_name
                )
                calendar_client = httpx.AsyncClient()
                oauth_client = HttpGoogleCalendarOAuthClient(
                    client_id=resolved_settings.calendar_client_id,
                    client_secret=calendar_client_secret,
                    client=calendar_client,
                )
                application.state.container.calendar_oauth = CalendarOAuthService(
                    application.state.container.repository,
                    secret_store,
                    oauth_client,
                    client_id=resolved_settings.calendar_client_id,
                    pkce_signing_key=resolved_settings.calendar_oauth_signing_key,
                )
            if application.state.container.telegram_calendar is None:
                oauth = application.state.container.calendar_oauth
                if oauth is None:
                    raise RuntimeError("Calendar OAuth service is unavailable")
                application.state.container.telegram_calendar = TelegramCalendarService(
                    application.state.container.repository,
                    oauth,
                    redirect_uri=resolved_settings.calendar_redirect_uri,
                )
        if resolved_settings.enable_calendar_actions and not calendar_client_secret:
            secret_store = application.state.container.secret_store
            if secret_store is None:
                raise RuntimeError("Calendar actions require a Secret Manager store")
            calendar_client_secret = await secret_store.access_secret(
                resource_name=resolved_settings.calendar_client_secret_resource_name
            )
        if resolved_settings.enable_gmail_connections:
            if application.state.container.gmail_oauth is None:
                secret_store = (
                    application.state.container.connection_secret_store
                    or application.state.container.secret_store
                )
                if secret_store is None:
                    raise RuntimeError("Gmail requires a Secret Manager store")
                gmail_client_secret = await secret_store.access_secret(
                    resource_name=resolved_settings.gmail_client_secret_resource_name
                )
                gmail_client = httpx.AsyncClient()
                application.state.container.gmail_oauth = GmailOAuthService(
                    application.state.container.repository,
                    secret_store,
                    HttpGoogleGmailOAuthClient(
                        client_id=resolved_settings.gmail_client_id,
                        client_secret=gmail_client_secret,
                        client=gmail_client,
                    ),
                    client_id=resolved_settings.gmail_client_id,
                    pkce_signing_key=resolved_settings.gmail_oauth_signing_key,
                )
            if application.state.container.telegram_gmail is None:
                oauth = application.state.container.gmail_oauth
                if oauth is None:
                    raise RuntimeError("Gmail OAuth service is unavailable")
                application.state.container.telegram_gmail = TelegramGmailService(
                    application.state.container.repository,
                    oauth,
                    redirect_uri=resolved_settings.gmail_redirect_uri,
                )
        if resolved_settings.enable_gmail_drafts and not gmail_client_secret:
            secret_store = application.state.container.secret_store
            if secret_store is None:
                raise RuntimeError("Gmail drafts require a Secret Manager store")
            gmail_client_secret = await secret_store.access_secret(
                resource_name=resolved_settings.gmail_client_secret_resource_name
            )
        if application.state.container.recovery is None:
            recovery_provider_factory = None
            recovery_quote_provider = None
            if resolved_settings.enable_duffel_quotes:
                quote_token = resolved_settings.duffel_access_token
                if not quote_token:
                    secret_store = application.state.container.secret_store
                    if secret_store is None:
                        raise RuntimeError("Duffel quotes require a Secret Manager store")
                    quote_token = await secret_store.access_secret(
                        resource_name=resolved_settings.duffel_access_token_secret_resource_name
                    )
                recovery_quote_provider = DuffelFlightQuoteClient(
                    access_token=quote_token,
                    client=httpx.AsyncClient(),
                )
            if resolved_settings.enable_calendar_actions:
                secret_store = application.state.container.secret_store
                if secret_store is None:
                    raise RuntimeError("Calendar actions require a Secret Manager store")
                calendar_api_client = httpx.AsyncClient()
                calendar_api = HttpGoogleCalendarApi(client=calendar_api_client)
            else:
                calendar_api = None
            if resolved_settings.enable_gmail_drafts:
                gmail_api_client = httpx.AsyncClient()
                gmail_api = HttpGoogleGmailDraftApi(client=gmail_api_client)
            else:
                gmail_api = None
            if resolved_settings.enable_calendar_actions or resolved_settings.enable_gmail_drafts:
                secret_store = (
                    application.state.container.connection_secret_store
                    or application.state.container.secret_store
                )
                if secret_store is None:
                    raise RuntimeError("Google actions require a Secret Manager store")

                def recovery_provider_factory(telegram_user_id: str) -> ActionProvider:
                    return TravelerGoogleActionProvider(
                        repository=application.state.container.repository,
                        secret_store=secret_store,
                        telegram_user_id=telegram_user_id,
                        calendar_client_id=(
                            resolved_settings.calendar_client_id
                            if resolved_settings.enable_calendar_actions
                            else None
                        ),
                        calendar_client_secret=(
                            calendar_client_secret
                            if resolved_settings.enable_calendar_actions
                            else None
                        ),
                        calendar_api=calendar_api,
                        calendar_id=resolved_settings.calendar_id,
                        gmail_client_id=(
                            resolved_settings.gmail_client_id
                            if resolved_settings.enable_gmail_drafts
                            else None
                        ),
                        gmail_client_secret=(
                            gmail_client_secret if resolved_settings.enable_gmail_drafts else None
                        ),
                        gmail_api=gmail_api,
                        http_client=calendar_api_client or gmail_api_client,
                    )

            application.state.container.recovery = RecoveryWorkflow(
                application.state.container.repository,
                provider=JudgeOnlyDemoProvider(application.state.container.repository),
                quote_provider=recovery_quote_provider,
                approval_tokens=(
                    ApprovalTokenManager(resolved_settings.approval_callback_signing_key)
                    if resolved_settings.approval_callback_signing_key
                    else None
                ),
                provider_factory=recovery_provider_factory,
            )
        if application.state.container.telegram_recovery is None:
            application.state.container.telegram_recovery = TelegramRecoveryService(
                application.state.container.repository,
                application.state.container.recovery,
            )
        if application.state.container.telegram_trips is None:
            application.state.container.telegram_trips = TelegramTripService(
                application.state.container.repository,
                pilot_enabled=resolved_settings.enable_pilot_trip,
                judge_mode=resolved_settings.enable_judge_mode,
                amadeus_enabled=resolved_settings.enable_amadeus_flight_monitoring,
                extractor=ItineraryExtractor(
                    model_id=resolved_settings.gemini_model_id or None,
                    vertex_project=resolved_settings.google_cloud_project or None,
                    vertex_location=resolved_settings.google_cloud_location or None,
                ),
            )
        if application.state.container.telegram_planning is None:
            planner = None
            if resolved_settings.enable_judge_mode:
                planner = VertexTripPlanner(
                    application.state.container.repository,
                    project=resolved_settings.google_cloud_project,
                    location=resolved_settings.google_cloud_location,
                    model=resolved_settings.gemini_model_id,
                    daily_limit=resolved_settings.judge_daily_vertex_calls,
                    daily_user_limit=resolved_settings.judge_daily_vertex_calls_per_user,
                    max_output_tokens=resolved_settings.judge_max_output_tokens,
                )
            application.state.container.telegram_planning = TelegramPlanningService(
                application.state.container.repository, planner=planner
            )
        if application.state.container.telegram_demo is None:
            application.state.container.telegram_demo = TelegramDemoService(
                application.state.container.repository,
                application.state.container.recovery,
            )
        if application.state.container.telegram_conversation is None:
            judge_chat = None
            if resolved_settings.enable_judge_mode:
                judge_chat = VertexJudgeChat(
                    application.state.container.repository,
                    project=resolved_settings.google_cloud_project,
                    location=resolved_settings.google_cloud_location,
                    model=resolved_settings.gemini_model_id,
                    daily_limit=resolved_settings.judge_daily_vertex_calls,
                    daily_user_limit=resolved_settings.judge_daily_vertex_calls_per_user,
                    max_output_tokens=resolved_settings.judge_max_output_tokens,
                )
            application.state.container.telegram_conversation = TelegramConversationService(
                application.state.container.repository,
                judge_chat=judge_chat,
                planning=application.state.container.telegram_planning,
            )
        if application.state.container.trip_watch is None and resolved_settings.enable_trip_watch:
            secret_store = application.state.container.secret_store or GoogleSecretManagerStore(
                resolved_settings.google_cloud_project
            )
            if resolved_settings.enable_judge_mode:
                grounder: WatchGrounder = JudgeGoogleSearchWatch(
                    application.state.container.repository,
                    project=resolved_settings.google_cloud_project,
                    location=resolved_settings.google_cloud_location,
                    model=resolved_settings.gemini_model_id,
                    daily_limit=resolved_settings.judge_daily_vertex_calls,
                    daily_user_limit=resolved_settings.judge_daily_vertex_calls_per_user,
                )
            else:
                grounder = PerTravelerGoogleSearchWatch(
                    repository=application.state.container.repository,
                    secret_store=secret_store,
                    project=resolved_settings.google_cloud_project,
                    location=resolved_settings.google_cloud_location,
                    model=resolved_settings.gemini_model_id,
                )
            if resolved_settings.enable_amadeus_flight_monitoring:
                amadeus_client = httpx.AsyncClient()
                grounder = AutonomousWatchGrounder(
                    search=grounder,
                    flight=AmadeusFlightWatch(
                        application.state.container.repository,
                        AmadeusFlightStatusClient(
                            client_id=resolved_settings.amadeus_client_id,
                            client_secret=resolved_settings.amadeus_client_secret,
                            client=amadeus_client,
                            base_url=resolved_settings.amadeus_base_url,
                        ),
                    ),
                )
            application.state.container.trip_watch = TripWatchWorkflow(
                application.state.container.repository,
                grounder,
                application.state.container.publisher,
            )
        if (
            application.state.container.ai_connections is None
            and resolved_settings.enable_byok_connections
        ):
            byok_client = httpx.AsyncClient()
            application.state.container.ai_connections = AiConnectionService(
                application.state.container.repository,
                GoogleSecretManagerStore(
                    resolved_settings.google_cloud_project,
                    resolved_settings.byok_secret_resource_name,
                ),
                GoogleGeminiKeyValidator(byok_client),
            )
        if (
            application.state.container.telegram_ai is None
            and application.state.container.ai_connections is not None
            and resolved_settings.connection_base_url
        ):
            application.state.container.telegram_ai = TelegramAiConnectionService(
                application.state.container.repository,
                application.state.container.ai_connections,
                resolved_settings.connection_base_url,
            )
        if (
            application.state.container.telegram_gateway is None
            and resolved_settings.telegram_bot_token
        ):
            telegram_client = httpx.AsyncClient()
            application.state.container.telegram_gateway = TelegramBotApiGateway(
                bot_token=resolved_settings.telegram_bot_token,
                client=telegram_client,
            )
        if resolved_settings.seed_demo_data:
            await application.state.container.repository.seed_trip(build_demo_trip())
        try:
            yield
        finally:
            if telegram_client is not None:
                await telegram_client.aclose()
            if byok_client is not None:
                await byok_client.aclose()
            if calendar_client is not None:
                await calendar_client.aclose()
            if calendar_api_client is not None:
                await calendar_api_client.aclose()
            if gmail_client is not None:
                await gmail_client.aclose()
            if gmail_api_client is not None:
                await gmail_api_client.aclose()
            if amadeus_client is not None:
                await amadeus_client.aclose()

    application = FastAPI(
        title="Trip Recovery Agent",
        version="0.1.0",
        lifespan=lifespan,
    )

    @application.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        # Cloud Run terminates TLS before the app. Only advertise HSTS when the
        # incoming request was HTTPS so local HTTP development is unaffected.
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

    @application.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    application.include_router(router)
    from app.api.simulator import simulator_api_router, simulator_router

    application.include_router(simulator_router)
    application.include_router(simulator_api_router)
    if resolved_settings.app_role == "all":
        application.include_router(telegram_router)
        application.include_router(connections_router)
    else:
        # The worker is private behind Cloud Run IAM. Keep Telegram handling off
        # the public route table while exposing an authenticated internal path
        # for the edge service's ID-token forward.
        application.include_router(telegram_router, prefix="/internal")
        application.include_router(worker_connections_router)
        application.include_router(calendar_worker_router)
        application.include_router(gmail_worker_router)
    return application


app = create_app()
