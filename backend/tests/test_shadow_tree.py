from __future__ import annotations

from datetime import UTC, datetime

from app.config import Settings
from app.demo_data import build_demo_trip, build_owned_demo_trip
from app.main import AppContainer, create_app
from app.models.enums import OnboardingStep
from app.models.shadow_tree import HoldStatus, HoldType, RiskLevel
from app.models.telegram import TravelerProfile
from app.services.memory import InMemoryIncidentRepository, LocalEventPublisher
from app.services.predictive_shadow_engine import PredictiveShadowEngine
from app.services.telegram_demo import TelegramDemoService
from app.workflows.impact_analysis import ImpactAnalysisWorkflow
from app.workflows.recovery import RecoveryWorkflow
from httpx import ASGITransport, AsyncClient

from tests.helpers import ValidInterpreter


def test_predictive_risk_assessment_on_tight_connection() -> None:
    trip = build_demo_trip()
    now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)

    tree = PredictiveShadowEngine.evaluate_trip_risk(trip, now=now)

    assert len(tree.assessments) == 1
    assessment = tree.assessments[0]
    assert assessment.hub_airport == "MUC"
    assert assessment.scheduled_buffer_minutes == 55
    assert assessment.required_buffer_minutes == 45
    assert assessment.probability_of_miss >= 0.65
    assert assessment.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)

    # Preemptive zero-cost hold created automatically
    assert len(tree.active_holds) == 1
    hold = tree.active_holds[0]
    assert hold.hold_type == HoldType.FARE_LOCK
    assert hold.status == HoldStatus.ACTIVE
    assert hold.cost_to_hold.minor_units == 0  # Free hold
    assert hold.locked_rebooking_price.minor_units == 3_400  # €34 locked price
    assert hold.surge_market_price.minor_units == 25_000  # €250 surge price
    assert hold.alternative_origin == "MUC"
    assert hold.alternative_destination == "LIS"


def test_inbound_delay_escalates_risk_to_critical() -> None:
    trip = build_demo_trip()
    now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)

    tree = PredictiveShadowEngine.evaluate_trip_risk(trip, now=now, inbound_delay_minutes=35)

    assessment = tree.assessments[0]
    assert assessment.risk_level == RiskLevel.CRITICAL
    assert assessment.probability_of_miss >= 0.85
    assert any("feeder flight" in f for f in assessment.risk_factors)


def test_hold_promotion_instant_zero_latency() -> None:
    trip = build_demo_trip()
    now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    tree = PredictiveShadowEngine.evaluate_trip_risk(trip, now=now)

    connection_id = tree.assessments[0].connection_id
    promoted_time = datetime(2026, 8, 20, 17, 30, tzinfo=UTC)

    new_tree, promoted = PredictiveShadowEngine.promote_hold(
        tree, connection_id=connection_id, promoted_at=promoted_time
    )

    assert promoted is not None
    assert promoted.status == HoldStatus.PROMOTED
    assert promoted.promoted_at == promoted_time
    assert new_tree.active_holds[0].status == HoldStatus.PROMOTED
    assert "promoted with zero latency" in new_tree.contingency_summary


async def test_telegram_demo_shadow_view() -> None:
    now = datetime(2026, 8, 20, tzinfo=UTC)
    repository = InMemoryIncidentRepository()
    traveler = TravelerProfile(
        user_id="telegram:user-shadow-1",
        telegram_user_id="user-shadow-1",
        telegram_chat_id="chat-shadow-1",
        onboarding_step=OnboardingStep.COMPLETE,
        created_at=now,
        updated_at=now,
    )
    await repository.save_traveler(traveler)
    trip_id = f"telegram-demo-trip:{traveler.telegram_user_id}"
    await repository.seed_trip(
        build_owned_demo_trip(owner_user_id=traveler.user_id, trip_id=trip_id)
    )

    demo_service = TelegramDemoService(repository, RecoveryWorkflow(repository))
    view = await demo_service.handle(
        telegram_user_id=traveler.telegram_user_id,
        telegram_chat_id=traveler.telegram_chat_id,
        callback_data="demo:shadow",
        update_id="up-shadow-1",
        now=now,
    )

    assert "PREDICTIVE SHADOW TREE EXECUTION" in view.text
    assert "BAYESIAN THREAT SHIELD" in view.text
    assert "Preemptive 24h Free Fare Lock" in view.text
    assert "€34.00" in view.text
    assert "Zero-Latency Recovery" in view.text


async def test_shadow_tree_api_routes() -> None:
    settings = Settings(pubsub_transport="local")
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(build_demo_trip())

    container = AppContainer(
        settings,
        repository,
        LocalEventPublisher(),
        ImpactAnalysisWorkflow(repository, ValidInterpreter()),
    )
    app = create_app(settings, container=container)
    app.state.container = container

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/internal/trips/demo-trip-001/shadow-tree")
        assert res.status_code == 200
        data = res.json()
        assert data["trip_id"] == "demo-trip-001"
        assert len(data["active_holds"]) == 1
        assert data["active_holds"][0]["locked_rebooking_price"]["minor_units"] == 3400

        scan_res = await client.post(
            "/internal/trips/demo-trip-001/predictive-scan?inbound_delay_minutes=30"
        )
        assert scan_res.status_code == 200
        scan_data = scan_res.json()
        assert scan_data["assessments"][0]["risk_level"] in ("HIGH", "CRITICAL")
