from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from app.demo_data import build_owned_demo_trip
from app.models.domain import DisruptionEvent, Incident
from app.models.enums import ApprovalStatus, IncidentStatus, OnboardingStep, PlanStatus
from app.models.money import Money
from app.models.policy import AutonomyPolicy
from app.models.telegram import TravelerProfile
from app.services.memory import InMemoryIncidentRepository
from app.services.telegram_recovery import RecoveryInteractionError, TelegramRecoveryService
from app.workflows.impact_analysis import ImpactAnalysisWorkflow
from app.workflows.recovery import RecoveryStartResult, RecoveryWorkflow

from tests.helpers import ValidInterpreter, disruption_event


async def prepared() -> tuple[
    InMemoryIncidentRepository, TelegramRecoveryService, str, str, datetime
]:
    now = datetime(2026, 8, 16, tzinfo=UTC)
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(
        build_owned_demo_trip(owner_user_id="telegram:101", trip_id="demo-trip-001")
    )
    await repository.save_traveler(
        TravelerProfile(
            user_id="telegram:101",
            telegram_user_id="101",
            telegram_chat_id="202",
            onboarding_step=OnboardingStep.COMPLETE,
            automatic_spending_enabled=True,
            incident_spending_limit=Money(currency="EUR", minor_units=2_000),
            active_policy_version=1,
            created_at=now,
            updated_at=now,
        )
    )
    impact = await ImpactAnalysisWorkflow(repository, ValidInterpreter()).process(
        disruption_event(event_id="telegram-recovery-001")
    )
    workflow = RecoveryWorkflow(repository)
    policy = AutonomyPolicy(
        policy_id="policy-telegram-recovery",
        user_id="telegram:101",
        version=1,
        automatic_spending_enabled=True,
        incident_spending_limit=Money(currency="EUR", minor_units=2_000),
        created_at=now,
        updated_at=now,
    )
    started = await workflow.start(
        incident_id=impact.incident_id,
        policy=policy,
        telegram_user_id="101",
        telegram_chat_id="202",
        now=now,
    )
    assert started.approval_callback_token is not None
    return (
        repository,
        TelegramRecoveryService(repository, workflow),
        started.approval_callback_token,
        impact.incident_id,
        now,
    )


async def test_details_is_read_only_and_approval_recovers_trip_once() -> None:
    repository, service, token, incident_id, now = await prepared()

    details = await service.handle_callback(
        callback_data=f"d:{token}",
        telegram_user_id="101",
        telegram_chat_id="202",
        update_id="details-1",
        now=now,
    )
    waiting = await repository.get_incident(incident_id)
    approved = await service.handle_callback(
        callback_data=f"a:{token}",
        telegram_user_id="101",
        telegram_chat_id="202",
        update_id="approve-1",
        now=now,
    )
    resume = next(
        record.command
        for record in repository.outbox.values()
        if record.command.type.value == "RESUME_AFTER_APPROVAL"
    )
    recovered_status = await service._workflow.process_command(  # noqa: SLF001
        command=resume,
        worker_id="telegram-test-worker",
        now=now,
    )
    recovered = await service.status_view(incident_id, recovered_status)
    duplicate = await service.handle_callback(
        callback_data=f"a:{token}",
        telegram_user_id="101",
        telegram_chat_id="202",
        update_id="approve-2",
        now=now,
    )

    assert "WHY THIS OPTION?" in details.text
    assert details.button_rows[0][0].callback_data == f"a:{token}"
    assert waiting is not None and waiting.status == IncidentStatus.WAITING_APPROVAL
    assert approved.text.startswith("Approval recorded")
    assert recovered.text.startswith("Trip recovered")
    assert duplicate.text == "Recovery already verified. Nothing else needs your approval."
    assert len(repository.effects) == 4


async def test_stolen_or_unknown_callback_cannot_mutate_recovery() -> None:
    repository, service, token, incident_id, now = await prepared()

    with pytest.raises(RecoveryInteractionError, match="another traveler"):
        await service.handle_callback(
            callback_data=f"a:{token}",
            telegram_user_id="999",
            telegram_chat_id="202",
            update_id="stolen-1",
            now=now,
        )
    with pytest.raises(RecoveryInteractionError, match="stale or unknown"):
        await service.handle_callback(
            callback_data="a:not-a-real-token",
            telegram_user_id="101",
            telegram_chat_id="202",
            update_id="unknown-1",
            now=now,
        )

    incident = await repository.get_incident(incident_id)
    assert incident is not None and incident.status == IncidentStatus.WAITING_APPROVAL


async def test_stop_declines_current_authority_without_flight_effect() -> None:
    repository, service, token, incident_id, now = await prepared()

    confirmation = await service.handle_callback(
        callback_data=f"c:{token}",
        telegram_user_id="101",
        telegram_chat_id="202",
        update_id="confirm-stop-1",
        now=now,
    )
    before_stop = await repository.get_incident(incident_id)
    stopped = await service.handle_callback(
        callback_data=f"s:{token}",
        telegram_user_id="101",
        telegram_chat_id="202",
        update_id="stop-1",
        now=now,
    )

    incident = await repository.get_incident(incident_id)
    approval = next(iter(repository.approvals.values()))
    assert confirmation.text.startswith("Stop this recovery?")
    assert before_stop is not None and before_stop.status == IncidentStatus.WAITING_APPROVAL
    assert stopped.text.startswith("Recovery stopped")
    assert stopped.button_rows[0][0].callback_data == f"r:{token}"
    assert incident is not None and incident.status == IncidentStatus.CANCELLED
    assert approval.status == ApprovalStatus.DECLINED
    assert len(repository.effects) == 3


async def test_resume_after_stop_supersedes_old_authority_and_enqueues_fresh_plan() -> None:
    repository, service, token, incident_id, now = await prepared()
    await service.handle_callback(
        callback_data=f"s:{token}",
        telegram_user_id="101",
        telegram_chat_id="202",
        update_id="stop-before-resume",
        now=now,
    )

    resumed = await service.handle_callback(
        callback_data=f"r:{token}",
        telegram_user_id="101",
        telegram_chat_id="202",
        update_id="resume-1",
        now=now,
    )
    command = next(
        record.command
        for record in repository.outbox.values()
        if record.command.type.value == "REPLAN"
        and record.command.payload.get("reason") == "resume"
    )
    status = await service._workflow.process_command(  # noqa: SLF001
        command=command,
        worker_id="resume-command-worker",
        now=now,
    )
    old_plan = repository.plans[(incident_id, 1)]
    current_plan = await repository.get_current_plan(incident_id)
    approval = next(iter(repository.approvals.values()))

    assert resumed.text.startswith("I’m checking the latest trip state")
    assert status == IncidentStatus.NOTIFYING
    assert old_plan.status == PlanStatus.SUPERSEDED
    assert current_plan is not None and current_plan.version == 2
    assert approval.status == ApprovalStatus.SUPERSEDED


async def test_concurrent_find_another_requests_create_one_replan() -> None:
    repository, service, token, incident_id, now = await prepared()

    results = await asyncio.gather(
        service.handle_callback(
            callback_data=f"f:{token}",
            telegram_user_id="101",
            telegram_chat_id="202",
            update_id="find-1",
            now=now,
        ),
        service.handle_callback(
            callback_data=f"f:{token}",
            telegram_user_id="101",
            telegram_chat_id="202",
            update_id="find-2",
            now=now,
        ),
    )
    replans = [
        record
        for record in repository.outbox.values()
        if record.command.type.value == "REPLAN"
        and record.command.payload.get("reason") == "find_another"
    ]
    incident = await repository.get_incident(incident_id)
    approval = next(iter(repository.approvals.values()))

    assert all(result.text.startswith("I’m checking the latest trip state") for result in results)
    assert len(replans) == 1
    assert incident is not None and incident.status == IncidentStatus.PLANNING
    assert approval.status == ApprovalStatus.SUPERSEDED


async def test_expired_details_are_read_only_and_hide_authority_controls() -> None:
    repository, service, token, incident_id, now = await prepared()
    approval_before = next(iter(repository.approvals.values())).model_copy(deep=True)

    details = await service.handle_callback(
        callback_data=f"d:{token}",
        telegram_user_id="101",
        telegram_chat_id="202",
        update_id="expired-details",
        now=approval_before.expires_at,
    )
    approval_after = next(iter(repository.approvals.values()))
    incident = await repository.get_incident(incident_id)

    assert details.button_rows == []
    assert approval_after == approval_before
    assert incident is not None and incident.status == IncidentStatus.WAITING_APPROVAL


async def test_approval_card_is_derived_from_actual_action_states() -> None:
    repository, service, token, incident_id, now = await prepared()
    approval = next(iter(repository.approvals.values()))
    plan = await repository.get_current_plan(incident_id)
    assert plan is not None

    view = await service.approval_view(
        RecoveryStartResult(
            plan=plan,
            approval=approval,
            incident_status=IncidentStatus.WAITING_APPROVAL,
            approval_callback_token=token,
        )
    )

    assert "— verified" in view.text
    assert "— pending approval" in view.text
    assert view.button_rows[1][0].callback_data == f"f:{token}"


async def test_recovered_incident_exposes_owner_bound_compensation_review() -> None:
    now = datetime(2026, 8, 23, tzinfo=UTC)
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(
        build_owned_demo_trip(owner_user_id="telegram:101", trip_id="claim-trip-001")
    )
    await repository.save_traveler(
        TravelerProfile(
            user_id="telegram:101",
            telegram_user_id="101",
            telegram_chat_id="202",
            onboarding_step=OnboardingStep.COMPLETE,
            created_at=now,
            updated_at=now,
        )
    )
    incident = Incident(
        incident_id="claim-incident-001",
        trip_id="claim-trip-001",
        external_event_id="grounded-delay-001",
        correlation_id="claim-correlation-001",
        trigger=DisruptionEvent(
            event_id="grounded-delay-001",
            trip_id="claim-trip-001",
            type="flight_delay",
            flight="LO351",
            old_arrival=datetime(2026, 8, 20, 18, 0, tzinfo=UTC),
            new_arrival=datetime(2026, 8, 20, 21, 15, tzinfo=UTC),
            context={
                "airline_fault": True,
                "source_links": ["https://carrier.example/status?q=<script>"],
                "source_timestamps": ["2026-08-20T21:16:00Z"],
            },
        ),
        status=IncidentStatus.RECOVERED,
    )
    repository.incidents[incident.incident_id] = incident
    service = TelegramRecoveryService(repository, RecoveryWorkflow(repository))

    recovered = await service.status_view(
        incident.incident_id,
        IncidentStatus.RECOVERED,
        telegram_user_id="101",
        telegram_chat_id="202",
    )
    claim = await service.claim_view(
        incident_id=incident.incident_id,
        telegram_user_id="101",
        telegram_chat_id="202",
    )

    assert recovered.button_rows[0][0].callback_data == "claim:claim-incident-001"
    assert "COMPENSATION CLAIM" in claim.text
    assert "Reviewable claim draft" in claim.text
    assert "Nothing is sent automatically" in claim.text
    assert "&lt;script&gt;" in claim.text

    with pytest.raises(RecoveryInteractionError, match="another traveler"):
        await service.claim_view(
            incident_id=incident.incident_id,
            telegram_user_id="999",
            telegram_chat_id="202",
        )
