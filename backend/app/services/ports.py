from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from app.models.ai_connection import AiConnection, AiConnectionHandoff
from app.models.calendar import CalendarConnection, CalendarOAuthState
from app.models.commands import OutboxRecord, WorkflowCommand, WorkflowCommandState
from app.models.domain import (
    DeterministicImpact,
    DisruptionEvent,
    Incident,
    TravelInterpretation,
    Trip,
)
from app.models.enums import ClaimKind, IncidentStatus, TripStatus
from app.models.expense import TripExpense
from app.models.finance import OpenFinancialItem
from app.models.gmail import GmailConnection, GmailOAuthState
from app.models.money import Money
from app.models.monitoring import MonitoringSubscription
from app.models.policy import AutonomyPolicy
from app.models.readiness import TripDocument
from app.models.recovery import ActionAttempt, ApprovalRequest, PlannedAction, RecoveryPlan
from app.models.telegram import (
    OutboundNotification,
    TelegramMessageReceipt,
    TelegramView,
    TravelerProfile,
)
from app.models.trip_intake import TripDraft
from app.models.watch import GroundedTravelSignal, TripWatchpoint


class ClaimResult:
    def __init__(self, kind: ClaimKind, incident_id: str) -> None:
        self.kind = kind
        self.incident_id = incident_id

    @property
    def acquired(self) -> bool:
        return self.kind in {ClaimKind.NEW, ClaimKind.RESUMED}


class EventPayloadConflict(ValueError):
    """An external event ID was reused with different authoritative content."""


class TripCreateConflict(ValueError):
    """A trip ID is already bound to a different immutable intake."""


class IncidentRepository(Protocol):
    async def seed_trip(self, trip: Trip) -> None: ...

    async def get_trip(self, trip_id: str) -> Trip | None: ...

    async def list_trips_for_owner(self, owner_user_id: str) -> list[Trip]: ...

    async def delete_traveler_data(self, telegram_user_id: str) -> list[str]:
        """Delete a traveler's persisted data and return secret resources to revoke."""
        ...

    async def create_trip_once(self, trip: Trip) -> bool: ...

    async def put_monitoring_subscription(self, subscription: MonitoringSubscription) -> bool: ...

    async def get_monitoring_subscription(
        self, subscription_id: str
    ) -> MonitoringSubscription | None: ...

    async def list_monitoring_subscriptions(self, trip_id: str) -> list[MonitoringSubscription]: ...

    async def update_monitoring_subscription(
        self, subscription: MonitoringSubscription, *, expected_fingerprint: str | None
    ) -> bool: ...

    async def put_watchpoint(self, watchpoint: TripWatchpoint) -> bool: ...

    async def get_watchpoint(self, watchpoint_id: str) -> TripWatchpoint | None: ...

    async def list_watchpoints(self, trip_id: str) -> list[TripWatchpoint]: ...

    async def list_due_watchpoints(self, now: datetime, *, limit: int) -> list[TripWatchpoint]: ...

    async def reschedule_watchpoint(
        self, watchpoint: TripWatchpoint, *, expected_due_at: datetime
    ) -> bool: ...

    async def put_grounded_signal(self, signal: GroundedTravelSignal) -> bool: ...

    async def list_unpublished_grounded_signals(
        self, *, limit: int
    ) -> list[GroundedTravelSignal]: ...

    async def mark_grounded_signal_published(
        self, *, signal: GroundedTravelSignal, published_at: datetime
    ) -> bool: ...

    async def get_trip_draft(self, telegram_user_id: str) -> TripDraft | None: ...

    async def save_trip_draft(
        self, *, draft: TripDraft, expected_version: int | None
    ) -> TripDraft | None: ...

    async def clear_trip_draft(
        self,
        *,
        telegram_user_id: str,
        telegram_chat_id: str,
        expected_version: int,
    ) -> bool: ...

    async def claim_event(
        self,
        *,
        event: DisruptionEvent,
        incident: Incident,
        worker_id: str,
        lease_expires_at: datetime,
    ) -> ClaimResult: ...

    async def get_incident(self, incident_id: str) -> Incident | None: ...

    async def mark_event_retryable(
        self, event_id: str, incident: Incident, error: str, failed_at: datetime
    ) -> bool: ...

    async def commit_impact(
        self,
        *,
        incident_id: str,
        expected_version: int,
        impact: DeterministicImpact,
        gemini_model_id: str,
        prompt_version: str,
        updated_at: datetime,
    ) -> Incident | None: ...

    async def complete_analysis(
        self,
        *,
        event_id: str,
        incident_id: str,
        expected_version: int,
        interpretation: TravelInterpretation,
        completed_at: datetime,
    ) -> Incident | None: ...

    async def transition_incident(
        self,
        *,
        incident_id: str,
        expected_version: int,
        from_states: set[IncidentStatus],
        to_state: IncidentStatus,
        updated_at: datetime,
    ) -> Incident | None: ...

    async def commit_plan(self, *, plan: RecoveryPlan, expected_incident_version: int) -> bool: ...

    async def get_current_plan(self, incident_id: str) -> RecoveryPlan | None: ...

    async def put_action(self, action: PlannedAction) -> bool: ...

    async def claim_action(
        self,
        *,
        action_id: str,
        worker_id: str,
        lease_expires_at: datetime,
        now: datetime,
    ) -> PlannedAction | None: ...

    async def get_action(self, action_id: str) -> PlannedAction | None: ...

    async def list_actions(self, incident_id: str) -> list[PlannedAction]: ...

    async def complete_action_and_create_effect_receipt(
        self,
        *,
        action_id: str,
        effect_key: str,
        provider_reference: str,
        completed_at: datetime,
        attempt: ActionAttempt | None = None,
    ) -> bool: ...

    async def fail_action(
        self,
        *,
        action_id: str,
        worker_id: str,
        retry_after: datetime | None,
        attempt: ActionAttempt,
    ) -> bool: ...

    async def record_action_attempt(self, attempt: ActionAttempt) -> bool: ...

    async def list_action_attempts(self, action_id: str) -> list[ActionAttempt]: ...

    async def mark_action_verified(self, *, action_id: str, verified: bool) -> bool: ...

    async def store_approval(self, approval: ApprovalRequest) -> bool: ...

    async def get_approval(self, approval_id: str) -> ApprovalRequest | None: ...

    async def get_approval_by_callback_token_hash(
        self, callback_token_hash: str
    ) -> ApprovalRequest | None: ...

    async def consume_approval(
        self,
        *,
        approval_id: str,
        callback_token_hash: str,
        telegram_user_id: str,
        telegram_chat_id: str,
        update_id: str,
        now: datetime,
        outbox: OutboxRecord,
    ) -> bool: ...

    async def decline_approval(
        self,
        *,
        approval_id: str,
        callback_token_hash: str,
        telegram_user_id: str,
        telegram_chat_id: str,
        update_id: str,
        now: datetime,
    ) -> bool: ...

    async def request_approval_replan(
        self,
        *,
        approval_id: str,
        callback_token_hash: str,
        telegram_user_id: str,
        telegram_chat_id: str,
        update_id: str,
        now: datetime,
        outbox: OutboxRecord,
        resume_cancelled: bool,
    ) -> bool: ...

    async def expire_approval_and_enqueue_replan(
        self,
        *,
        approval_id: str,
        now: datetime,
        outbox: OutboxRecord,
    ) -> bool: ...

    async def claim_telegram_update(self, *, update_id: str, payload_hash: str) -> bool: ...

    async def claim_telegram_rate_slot(
        self,
        *,
        telegram_user_id: str,
        update_kind: str,
        window_started_at: datetime,
        limit: int,
    ) -> bool: ...

    async def enqueue_outbox_once(self, outbox: OutboxRecord) -> bool: ...

    async def get_outbox(self, outbox_id: str) -> OutboxRecord | None: ...

    async def list_pending_outbox(self, *, limit: int = 100) -> list[OutboxRecord]: ...

    async def mark_outbox_published(self, *, outbox_id: str, published_at: datetime) -> bool: ...

    async def claim_workflow_command(
        self,
        *,
        command: WorkflowCommand,
        worker_id: str,
        lease_expires_at: datetime,
        now: datetime,
    ) -> bool: ...

    async def complete_workflow_command(
        self, *, command_id: str, worker_id: str, completed_at: datetime
    ) -> bool: ...

    async def get_workflow_command_state(self, command_id: str) -> WorkflowCommandState | None: ...

    async def apply_demo_provider_effect(
        self,
        *,
        resource_id: str,
        effect_key: str,
        desired_state: dict[str, object],
    ) -> bool: ...

    async def get_demo_provider_state(self, resource_id: str) -> dict[str, object] | None: ...

    async def get_traveler(self, telegram_user_id: str) -> TravelerProfile | None: ...

    async def get_traveler_by_user_id(self, user_id: str) -> TravelerProfile | None: ...

    async def save_traveler(self, traveler: TravelerProfile) -> None: ...

    async def activate_traveler_policy(
        self, *, traveler: TravelerProfile, policy: AutonomyPolicy
    ) -> bool: ...

    async def get_traveler_policy(self, *, user_id: str, version: int) -> AutonomyPolicy | None: ...

    async def get_notification(self, notification_id: str) -> OutboundNotification | None: ...

    async def store_notification_intent(self, notification: OutboundNotification) -> bool: ...

    async def mark_notification_sent(
        self, *, notification_id: str, message_id: int, sent_at: datetime
    ) -> bool: ...

    async def mark_notification_unknown(
        self, *, notification_id: str, unknown_at: datetime
    ) -> bool: ...

    async def mark_notification_blocked(
        self, *, notification_id: str, blocked_at: datetime, failure_code: str
    ) -> bool: ...

    async def store_ai_handoff(self, handoff: AiConnectionHandoff) -> bool: ...

    async def consume_ai_handoff(
        self,
        *,
        state_hash: str,
        telegram_user_id: str,
        telegram_chat_id: str,
        now: datetime,
    ) -> AiConnectionHandoff | None: ...

    async def get_ai_connection(self, telegram_user_id: str) -> AiConnection | None: ...

    async def save_ai_connection(self, connection: AiConnection) -> None: ...

    async def store_calendar_oauth_state(self, state: CalendarOAuthState) -> bool: ...

    async def get_calendar_oauth_state(self, state_hash: str) -> CalendarOAuthState | None: ...

    async def consume_calendar_oauth_state(
        self,
        *,
        state_hash: str,
        telegram_user_id: str,
        telegram_chat_id: str,
        redirect_uri: str,
        code_verifier_hash: str,
        now: datetime,
    ) -> CalendarOAuthState | None: ...

    async def get_calendar_connection(self, telegram_user_id: str) -> CalendarConnection | None: ...

    async def save_calendar_connection(self, connection: CalendarConnection) -> None: ...

    async def store_gmail_oauth_state(self, state: GmailOAuthState) -> bool: ...

    async def get_gmail_oauth_state(self, state_hash: str) -> GmailOAuthState | None: ...

    async def consume_gmail_oauth_state(
        self,
        *,
        state_hash: str,
        telegram_user_id: str,
        telegram_chat_id: str,
        redirect_uri: str,
        code_verifier_hash: str,
        now: datetime,
    ) -> GmailOAuthState | None: ...

    async def get_gmail_connection(self, telegram_user_id: str) -> GmailConnection | None: ...

    async def save_gmail_connection(self, connection: GmailConnection) -> None: ...

    async def save_expense_once(self, expense: TripExpense) -> bool: ...

    async def get_expense(self, expense_id: str) -> TripExpense | None: ...

    async def list_expenses(self, trip_id: str) -> list[TripExpense]: ...

    async def save_trip_document_once(self, document: TripDocument) -> bool: ...

    async def list_trip_documents(self, trip_id: str) -> list[TripDocument]: ...

    async def save_financial_item_once(self, item: OpenFinancialItem) -> bool: ...

    async def list_financial_items(self, trip_id: str) -> list[OpenFinancialItem]: ...

    async def settle_financial_item(
        self,
        *,
        financial_item_id: str,
        owner_user_id: str,
        actual_amount: Money,
        settled_at: datetime,
    ) -> OpenFinancialItem | None: ...

    async def set_trip_status(
        self,
        *,
        trip_id: str,
        owner_user_id: str,
        status: TripStatus,
        updated_at: datetime,
    ) -> bool: ...


class SecretStore(Protocol):
    async def put_user_secret(self, *, user_id: str, value: str) -> str: ...

    async def delete_secret(self, *, resource_name: str) -> None: ...

    async def access_secret(self, *, resource_name: str) -> str: ...


class GeminiKeyValidator(Protocol):
    async def validate(self, api_key: str) -> bool: ...


class EventPublisher(Protocol):
    async def publish(self, event: DisruptionEvent) -> str: ...


class WorkflowCommandPublisher(Protocol):
    async def publish_command(self, command: WorkflowCommand) -> str: ...


class TravelInterpreter(Protocol):
    model_id: str
    prompt_version: str

    async def interpret(
        self, event: DisruptionEvent, trip: Trip, deterministic_impact: Any
    ) -> dict[str, Any]: ...


class TelegramGateway(Protocol):
    async def send_message(self, *, chat_id: str, view: TelegramView) -> TelegramMessageReceipt: ...

    async def edit_message(
        self, *, chat_id: str, message_id: int, view: TelegramView
    ) -> TelegramMessageReceipt: ...

    async def answer_callback_query(
        self,
        *,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> None: ...


class TelegramMediaGateway(Protocol):
    async def download_file(
        self, *, file_id: str, file_name: str | None, mime_type: str | None, max_bytes: int
    ) -> Any: ...
