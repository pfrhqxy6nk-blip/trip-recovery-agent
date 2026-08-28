from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
from typing import Any

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
from app.models.enums import (
    ActionStatus,
    ApprovalStatus,
    ClaimKind,
    EventProcessingStatus,
    IncidentStatus,
    OutboxStatus,
    PlanStatus,
    TripStatus,
    WorkflowCommandStatus,
    WorkflowCommandType,
)
from app.models.expense import TripExpense
from app.models.finance import OpenFinancialItem
from app.models.gmail import GmailConnection, GmailOAuthState
from app.models.money import Money
from app.models.monitoring import MonitoringSubscription
from app.models.policy import AutonomyPolicy
from app.models.readiness import TripDocument
from app.models.recovery import ActionAttempt, ApprovalRequest, PlannedAction, RecoveryPlan
from app.models.telegram import OutboundNotification, TravelerProfile
from app.models.trip_intake import TripDraft
from app.models.watch import GroundedTravelSignal, TripWatchpoint
from app.services.canonical_hash import canonical_hash, grounded_signal_hash
from app.services.ports import ClaimResult, EventPayloadConflict, TripCreateConflict


class InMemoryIncidentRepository:
    """Process-local adapter used by tests and explicit local development mode."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.trips: dict[str, Trip] = {}
        self.trip_drafts: dict[str, TripDraft] = {}
        self.monitoring_subscriptions: dict[str, MonitoringSubscription] = {}
        self.watchpoints: dict[str, TripWatchpoint] = {}
        self.grounded_signals: dict[str, GroundedTravelSignal] = {}
        self.incidents: dict[str, Incident] = {}
        self.processed_events: dict[str, dict[str, Any]] = {}
        self.plans: dict[tuple[str, int], RecoveryPlan] = {}
        self.actions: dict[str, PlannedAction] = {}
        self.action_attempts: dict[str, ActionAttempt] = {}
        self.approvals: dict[str, ApprovalRequest] = {}
        self.effects: dict[str, dict[str, Any]] = {}
        self.telegram_updates: dict[str, str] = {}
        self.telegram_rate_limits: dict[tuple[str, str, datetime], int] = {}
        self.outbox: dict[str, OutboxRecord] = {}
        self.workflow_commands: dict[str, WorkflowCommandState] = {}
        self.demo_provider_state: dict[str, dict[str, object]] = {}
        self.travelers: dict[str, TravelerProfile] = {}
        self.policies: dict[tuple[str, int], AutonomyPolicy] = {}
        self.notifications: dict[str, OutboundNotification] = {}
        self.ai_handoffs: dict[str, AiConnectionHandoff] = {}
        self.ai_connections: dict[str, AiConnection] = {}
        self.calendar_oauth_states: dict[str, CalendarOAuthState] = {}
        self.calendar_connections: dict[str, CalendarConnection] = {}
        self.gmail_oauth_states: dict[str, GmailOAuthState] = {}
        self.gmail_connections: dict[str, GmailConnection] = {}
        self.expenses: dict[str, TripExpense] = {}
        self.trip_documents: dict[str, TripDocument] = {}
        self.financial_items: dict[str, OpenFinancialItem] = {}

    async def seed_trip(self, trip: Trip) -> None:
        async with self._lock:
            self.trips.setdefault(trip.trip_id, deepcopy(trip))

    async def get_trip(self, trip_id: str) -> Trip | None:
        async with self._lock:
            trip = self.trips.get(trip_id)
            return deepcopy(trip) if trip else None

    async def list_trips_for_owner(self, owner_user_id: str) -> list[Trip]:
        async with self._lock:
            return [
                deepcopy(trip)
                for trip in self.trips.values()
                if trip.owner_user_id == owner_user_id
            ]

    async def delete_traveler_data(self, telegram_user_id: str) -> list[str]:
        """Remove all in-memory records owned by one Telegram identity."""
        async with self._lock:
            traveler = self.travelers.get(telegram_user_id)
            if traveler is None:
                return []
            owner_user_id = traveler.user_id
            trip_ids = {
                trip.trip_id for trip in self.trips.values() if trip.owner_user_id == owner_user_id
            }
            incident_ids = {
                incident.incident_id
                for incident in self.incidents.values()
                if incident.trip_id in trip_ids
            }
            action_ids = {
                action.action_id
                for action in self.actions.values()
                if action.incident_id in incident_ids
            }
            deleted_watchpoint_ids = {
                key for key, value in self.watchpoints.items() if value.trip_id in trip_ids
            }
            self.trips = {key: value for key, value in self.trips.items() if key not in trip_ids}
            self.watchpoints = {
                key: value
                for key, value in self.watchpoints.items()
                if value.trip_id not in trip_ids
            }
            self.monitoring_subscriptions = {
                key: value
                for key, value in self.monitoring_subscriptions.items()
                if value.trip_id not in trip_ids
            }
            self.grounded_signals = {
                key: value
                for key, value in self.grounded_signals.items()
                if value.watchpoint_id not in deleted_watchpoint_ids
            }
            self.incidents = {
                key: value for key, value in self.incidents.items() if key not in incident_ids
            }
            self.processed_events = {
                key: value
                for key, value in self.processed_events.items()
                if value.get("incident_id") not in incident_ids
            }
            self.plans = {
                key: value for key, value in self.plans.items() if key[0] not in incident_ids
            }
            self.actions = {
                key: value for key, value in self.actions.items() if key not in action_ids
            }
            self.action_attempts = {
                key: value
                for key, value in self.action_attempts.items()
                if value.action_id not in action_ids
            }
            self.approvals = {
                key: value
                for key, value in self.approvals.items()
                if value.incident_id not in incident_ids
            }
            self.effects = {
                key: value
                for key, value in self.effects.items()
                if value.get("action_id") not in action_ids
            }
            self.outbox = {
                key: value
                for key, value in self.outbox.items()
                if value.command.incident_id not in incident_ids
            }
            self.workflow_commands = {
                key: value
                for key, value in self.workflow_commands.items()
                if value.command.incident_id not in incident_ids
            }
            self.notifications = {
                key: value
                for key, value in self.notifications.items()
                if value.incident_id not in incident_ids
            }
            self.trip_documents = {
                key: value
                for key, value in self.trip_documents.items()
                if value.trip_id not in trip_ids
            }
            self.expenses = {
                key: value for key, value in self.expenses.items() if value.trip_id not in trip_ids
            }
            self.financial_items = {
                key: value
                for key, value in self.financial_items.items()
                if value.trip_id not in trip_ids
            }
            self.trip_drafts.pop(telegram_user_id, None)
            self.policies = {
                key: value for key, value in self.policies.items() if key[0] != owner_user_id
            }
            self.ai_handoffs = {
                key: value
                for key, value in self.ai_handoffs.items()
                if value.telegram_user_id != telegram_user_id
            }
            ai_connection = self.ai_connections.pop(telegram_user_id, None)
            self.calendar_oauth_states = {
                key: value
                for key, value in self.calendar_oauth_states.items()
                if value.telegram_user_id != telegram_user_id
            }
            calendar = self.calendar_connections.pop(telegram_user_id, None)
            self.gmail_oauth_states = {
                key: value
                for key, value in self.gmail_oauth_states.items()
                if value.telegram_user_id != telegram_user_id
            }
            gmail = self.gmail_connections.pop(telegram_user_id, None)
            self.travelers.pop(telegram_user_id, None)
            self.telegram_rate_limits = {
                key: value
                for key, value in self.telegram_rate_limits.items()
                if key[0] != telegram_user_id
            }
            return [
                resource_name
                for connection in (calendar, gmail)
                if connection is not None
                for resource_name in [connection.secret_resource_name]
                if resource_name
            ] + [
                # AI credentials use the separate BYOK secret store.
                # The resource is returned to the caller before its metadata is removed.
                resource_name
                for resource_name in [getattr(ai_connection, "secret_resource_name", None)]
                if resource_name
            ]

    async def create_trip_once(self, trip: Trip) -> bool:
        async with self._lock:
            existing = self.trips.get(trip.trip_id)
            if existing is None:
                self.trips[trip.trip_id] = deepcopy(trip)
                return True
            if (
                existing.owner_user_id != trip.owner_user_id
                or existing.intake_hash != trip.intake_hash
            ):
                raise TripCreateConflict("trip ID is already bound to different intake data")
            return False

    async def put_monitoring_subscription(self, subscription: MonitoringSubscription) -> bool:
        async with self._lock:
            existing = self.monitoring_subscriptions.get(subscription.subscription_id)
            if existing is None:
                self.monitoring_subscriptions[subscription.subscription_id] = (
                    subscription.model_copy(deep=True)
                )
                return True
            return existing == subscription

    async def get_monitoring_subscription(
        self, subscription_id: str
    ) -> MonitoringSubscription | None:
        async with self._lock:
            stored = self.monitoring_subscriptions.get(subscription_id)
            return stored.model_copy(deep=True) if stored is not None else None

    async def list_monitoring_subscriptions(self, trip_id: str) -> list[MonitoringSubscription]:
        async with self._lock:
            return [
                subscription.model_copy(deep=True)
                for subscription in self.monitoring_subscriptions.values()
                if subscription.trip_id == trip_id
            ]

    async def update_monitoring_subscription(
        self, subscription: MonitoringSubscription, *, expected_fingerprint: str | None
    ) -> bool:
        async with self._lock:
            current = self.monitoring_subscriptions.get(subscription.subscription_id)
            if current is None or current.last_snapshot_fingerprint != expected_fingerprint:
                return False
            self.monitoring_subscriptions[subscription.subscription_id] = subscription.model_copy(
                deep=True
            )
            return True

    async def put_watchpoint(self, watchpoint: TripWatchpoint) -> bool:
        async with self._lock:
            existing = self.watchpoints.get(watchpoint.watchpoint_id)
            if existing is None:
                self.watchpoints[watchpoint.watchpoint_id] = watchpoint.model_copy(deep=True)
                return True
            return existing == watchpoint

    async def list_watchpoints(self, trip_id: str) -> list[TripWatchpoint]:
        async with self._lock:
            return [
                watchpoint.model_copy(deep=True)
                for watchpoint in self.watchpoints.values()
                if watchpoint.trip_id == trip_id
            ]

    async def get_watchpoint(self, watchpoint_id: str) -> TripWatchpoint | None:
        async with self._lock:
            watchpoint = self.watchpoints.get(watchpoint_id)
            return watchpoint.model_copy(deep=True) if watchpoint is not None else None

    async def list_due_watchpoints(self, now: datetime, *, limit: int) -> list[TripWatchpoint]:
        async with self._lock:
            return [
                watchpoint.model_copy(deep=True)
                for watchpoint in sorted(self.watchpoints.values(), key=lambda value: value.due_at)
                if watchpoint.due_at <= now
            ][:limit]

    async def reschedule_watchpoint(
        self, watchpoint: TripWatchpoint, *, expected_due_at: datetime
    ) -> bool:
        async with self._lock:
            current = self.watchpoints.get(watchpoint.watchpoint_id)
            if current is None or current.due_at != expected_due_at:
                return False
            self.watchpoints[watchpoint.watchpoint_id] = watchpoint.model_copy(deep=True)
            return True

    async def put_grounded_signal(self, signal: GroundedTravelSignal) -> bool:
        async with self._lock:
            key = f"{signal.watchpoint_id}:{grounded_signal_hash(signal)}"
            if key in self.grounded_signals:
                return False
            self.grounded_signals[key] = signal.model_copy(deep=True)
            return True

    async def list_unpublished_grounded_signals(self, *, limit: int) -> list[GroundedTravelSignal]:
        async with self._lock:
            return [
                signal.model_copy(deep=True)
                for signal in sorted(
                    self.grounded_signals.values(), key=lambda value: value.observed_at
                )
                if signal.published_at is None
            ][:limit]

    async def mark_grounded_signal_published(
        self, *, signal: GroundedTravelSignal, published_at: datetime
    ) -> bool:
        async with self._lock:
            key = f"{signal.watchpoint_id}:{grounded_signal_hash(signal)}"
            current = self.grounded_signals.get(key)
            if current is None:
                return False
            if current.published_at is not None:
                return True
            self.grounded_signals[key] = current.model_copy(update={"published_at": published_at})
            return True

    async def get_trip_draft(self, telegram_user_id: str) -> TripDraft | None:
        async with self._lock:
            draft = self.trip_drafts.get(telegram_user_id)
            return draft.model_copy(deep=True) if draft is not None else None

    async def save_trip_draft(
        self, *, draft: TripDraft, expected_version: int | None
    ) -> TripDraft | None:
        async with self._lock:
            current = self.trip_drafts.get(draft.telegram_user_id)
            if current is None:
                if expected_version is not None:
                    return None
                stored = draft.model_copy(update={"version": 1}, deep=True)
            else:
                if (
                    expected_version != current.version
                    or current.owner_user_id != draft.owner_user_id
                    or current.telegram_chat_id != draft.telegram_chat_id
                ):
                    return None
                stored = draft.model_copy(update={"version": current.version + 1}, deep=True)
            self.trip_drafts[draft.telegram_user_id] = stored
            return stored.model_copy(deep=True)

    async def clear_trip_draft(
        self,
        *,
        telegram_user_id: str,
        telegram_chat_id: str,
        expected_version: int,
    ) -> bool:
        async with self._lock:
            current = self.trip_drafts.get(telegram_user_id)
            if (
                current is None
                or current.telegram_chat_id != telegram_chat_id
                or current.version != expected_version
            ):
                return False
            del self.trip_drafts[telegram_user_id]
            return True

    async def claim_event(
        self,
        *,
        event: DisruptionEvent,
        incident: Incident,
        worker_id: str,
        lease_expires_at: datetime,
    ) -> ClaimResult:
        async with self._lock:
            record = self.processed_events.get(event.event_id)
            now = incident.updated_at
            payload_hash = canonical_hash(event)
            if record is None:
                trip = self.trips.get(event.trip_id)
                if trip is None:
                    raise KeyError(f"trip {event.trip_id!r} does not exist")
                trip = deepcopy(trip)
                trip.active_incident_id = incident.incident_id
                trip.updated_at = now
                self.trips[event.trip_id] = trip
                self.processed_events[event.event_id] = {
                    "event_id": event.event_id,
                    "incident_id": incident.incident_id,
                    "status": EventProcessingStatus.PROCESSING,
                    "lease_owner": worker_id,
                    "lease_expires_at": lease_expires_at,
                    "attempts": 1,
                    "claimed_at": now,
                    "payload_hash": payload_hash,
                }
                incident.lease_owner = worker_id
                incident.lease_expires_at = lease_expires_at
                self.incidents[incident.incident_id] = deepcopy(incident)
                return ClaimResult(ClaimKind.NEW, incident.incident_id)

            existing_payload_hash = record.get("payload_hash")
            if existing_payload_hash is not None and existing_payload_hash != payload_hash:
                raise EventPayloadConflict(
                    f"event ID {event.event_id!r} was reused with a new payload"
                )

            incident_id = str(record["incident_id"])
            if record["status"] == EventProcessingStatus.COMPLETED:
                return ClaimResult(ClaimKind.COMPLETED, incident_id)

            current_lease = record.get("lease_expires_at")
            if (
                record["status"] == EventProcessingStatus.PROCESSING
                and current_lease is not None
                and current_lease > now
            ):
                return ClaimResult(ClaimKind.IN_PROGRESS, incident_id)

            record.update(
                status=EventProcessingStatus.PROCESSING,
                lease_owner=worker_id,
                lease_expires_at=lease_expires_at,
                attempts=int(record["attempts"]) + 1,
            )
            existing = self.incidents[incident_id]
            existing.lease_owner = worker_id
            existing.lease_expires_at = lease_expires_at
            existing.retry_count += 1
            existing.status = IncidentStatus.RECEIVED
            existing.last_error = None
            existing.updated_at = now
            self.incidents[incident_id] = deepcopy(existing)
            return ClaimResult(ClaimKind.RESUMED, incident_id)

    async def get_incident(self, incident_id: str) -> Incident | None:
        async with self._lock:
            incident = self.incidents.get(incident_id)
            return deepcopy(incident) if incident else None

    async def mark_event_retryable(
        self, event_id: str, incident: Incident, error: str, failed_at: datetime
    ) -> bool:
        async with self._lock:
            current = self.incidents.get(incident.incident_id)
            if current is None or current.version != incident.version:
                return False
            record = self.processed_events[event_id]
            record.update(
                status=EventProcessingStatus.RETRYABLE,
                last_error=error,
                failed_at=failed_at,
                lease_owner=None,
                lease_expires_at=None,
            )
            failed = deepcopy(incident)
            failed.version += 1
            failed.updated_at = failed_at
            self.incidents[incident.incident_id] = failed
            return True

    async def commit_impact(
        self,
        *,
        incident_id: str,
        expected_version: int,
        impact: DeterministicImpact,
        gemini_model_id: str,
        prompt_version: str,
        updated_at: datetime,
    ) -> Incident | None:
        async with self._lock:
            current = self.incidents.get(incident_id)
            if (
                current is None
                or current.version != expected_version
                or current.status != IncidentStatus.ANALYZING
            ):
                return None
            committed = deepcopy(current)
            committed.deterministic_impact = impact.model_copy(deep=True)
            committed.gemini_model_id = gemini_model_id
            committed.prompt_version = prompt_version
            committed.version += 1
            committed.updated_at = updated_at
            self.incidents[incident_id] = deepcopy(committed)
            return committed

    async def complete_analysis(
        self,
        *,
        event_id: str,
        incident_id: str,
        expected_version: int,
        interpretation: TravelInterpretation,
        completed_at: datetime,
    ) -> Incident | None:
        async with self._lock:
            current = self.incidents.get(incident_id)
            event = self.processed_events.get(event_id)
            if (
                current is None
                or event is None
                or current.version != expected_version
                or current.status != IncidentStatus.ANALYZING
                or event.get("incident_id") != incident_id
            ):
                return None
            completed = deepcopy(current)
            completed.interpretation = interpretation.model_copy(deep=True)
            completed.status = IncidentStatus.PLANNING
            completed.analysis_completed_at = completed_at
            completed.last_error = None
            completed.lease_owner = None
            completed.lease_expires_at = None
            completed.version += 1
            completed.updated_at = completed_at
            self.incidents[incident_id] = deepcopy(completed)
            event.update(
                status=EventProcessingStatus.COMPLETED,
                completed_at=completed_at,
                lease_owner=None,
                lease_expires_at=None,
            )
            return completed

    async def transition_incident(
        self,
        *,
        incident_id: str,
        expected_version: int,
        from_states: set[IncidentStatus],
        to_state: IncidentStatus,
        updated_at: datetime,
    ) -> Incident | None:
        async with self._lock:
            current = self.incidents.get(incident_id)
            if (
                current is None
                or current.version != expected_version
                or current.status not in from_states
            ):
                return None
            transitioned = deepcopy(current)
            transitioned.status = to_state
            if to_state == IncidentStatus.ANALYZING:
                transitioned.analysis_started_at = updated_at
            transitioned.version += 1
            transitioned.updated_at = updated_at
            self.incidents[incident_id] = deepcopy(transitioned)
            return transitioned

    async def commit_plan(self, *, plan: RecoveryPlan, expected_incident_version: int) -> bool:
        async with self._lock:
            incident = self.incidents.get(plan.incident_id)
            key = (plan.incident_id, plan.version)
            if incident is None or incident.version != expected_incident_version:
                return False
            existing = self.plans.get(key)
            if existing is not None:
                return existing.plan_hash == plan.plan_hash
            for stored_key, stored_plan in tuple(self.plans.items()):
                if (
                    stored_key[0] == plan.incident_id
                    and stored_plan.version < plan.version
                    and stored_plan.status == PlanStatus.CURRENT
                ):
                    self.plans[stored_key] = stored_plan.model_copy(
                        update={"status": PlanStatus.SUPERSEDED},
                        deep=True,
                    )
            self.plans[key] = deepcopy(plan)
            incident.version += 1
            self.incidents[plan.incident_id] = deepcopy(incident)
            return True

    async def get_current_plan(self, incident_id: str) -> RecoveryPlan | None:
        async with self._lock:
            candidates = [
                plan
                for (stored_incident_id, _), plan in self.plans.items()
                if stored_incident_id == incident_id and plan.status.value == "CURRENT"
            ]
            if not candidates:
                return None
            return max(candidates, key=lambda plan: plan.version).model_copy(deep=True)

    async def put_action(self, action: PlannedAction) -> bool:
        async with self._lock:
            existing = self.actions.get(action.action_id)
            if existing is not None:
                return existing.effect_key == action.effect_key
            self.actions[action.action_id] = deepcopy(action)
            return True

    async def claim_action(
        self,
        *,
        action_id: str,
        worker_id: str,
        lease_expires_at: datetime,
        now: datetime,
    ) -> PlannedAction | None:
        async with self._lock:
            action = self.actions.get(action_id)
            reclaimable = (
                action is not None
                and action.execution_status == ActionStatus.LEASED
                and action.lease_expires_at is not None
                and action.lease_expires_at <= now
            )
            retryable = (
                action is not None
                and action.execution_status == ActionStatus.FAILED_RETRYABLE
                and (action.retry_after is None or action.retry_after <= now)
            )
            if action is None or (
                action.execution_status != ActionStatus.PENDING
                and not reclaimable
                and not retryable
            ):
                return None
            claimed = action.model_copy(deep=True)
            claimed.execution_status = ActionStatus.LEASED
            claimed.lease_owner = worker_id
            claimed.lease_started_at = now
            claimed.lease_expires_at = lease_expires_at
            claimed.retry_after = None
            claimed.attempt_count += 1
            self.actions[action_id] = claimed.model_copy(deep=True)
            return claimed

    async def get_action(self, action_id: str) -> PlannedAction | None:
        async with self._lock:
            action = self.actions.get(action_id)
            return action.model_copy(deep=True) if action is not None else None

    async def list_actions(self, incident_id: str) -> list[PlannedAction]:
        async with self._lock:
            return [
                action.model_copy(deep=True)
                for action in self.actions.values()
                if action.incident_id == incident_id
            ]

    async def complete_action_and_create_effect_receipt(
        self,
        *,
        action_id: str,
        effect_key: str,
        provider_reference: str,
        completed_at: datetime,
        attempt: ActionAttempt | None = None,
    ) -> bool:
        async with self._lock:
            action = self.actions.get(action_id)
            if action is None or action.effect_key != effect_key:
                return False
            if attempt is not None:
                existing_attempt = self.action_attempts.get(attempt.attempt_id)
                if existing_attempt is not None and existing_attempt != attempt:
                    return False
            effect = self.effects.get(effect_key)
            if effect is not None:
                if action.execution_status != ActionStatus.LEASED:
                    return action.execution_status in {
                        ActionStatus.SUCCEEDED,
                        ActionStatus.VERIFIED,
                    }
                completed = action.model_copy(deep=True)
                completed.execution_status = ActionStatus.SUCCEEDED
                completed.provider_reference = str(effect.get("provider_reference"))
                completed.lease_owner = None
                completed.lease_started_at = None
                completed.lease_expires_at = None
                self.actions[action_id] = completed
                if attempt is not None:
                    self.action_attempts.setdefault(attempt.attempt_id, deepcopy(attempt))
                return True
            if action.execution_status != ActionStatus.LEASED:
                return False
            completed = action.model_copy(deep=True)
            completed.execution_status = ActionStatus.SUCCEEDED
            completed.provider_reference = provider_reference
            completed.lease_owner = None
            completed.lease_started_at = None
            completed.lease_expires_at = None
            self.actions[action_id] = completed
            self.effects[effect_key] = {
                "action_id": action_id,
                "provider_reference": provider_reference,
                "completed_at": completed_at,
            }
            if attempt is not None:
                self.action_attempts.setdefault(attempt.attempt_id, deepcopy(attempt))
            return True

    async def fail_action(
        self,
        *,
        action_id: str,
        worker_id: str,
        retry_after: datetime | None,
        attempt: ActionAttempt,
    ) -> bool:
        async with self._lock:
            action = self.actions.get(action_id)
            if (
                action is None
                or action.execution_status != ActionStatus.LEASED
                or action.lease_owner != worker_id
            ):
                return False
            existing_attempt = self.action_attempts.get(attempt.attempt_id)
            if existing_attempt is not None and existing_attempt != attempt:
                return False
            failed = action.model_copy(deep=True)
            failed.execution_status = (
                ActionStatus.FAILED_RETRYABLE
                if retry_after is not None
                else ActionStatus.FAILED_TERMINAL
            )
            failed.lease_owner = None
            failed.lease_started_at = None
            failed.lease_expires_at = None
            failed.retry_after = retry_after
            self.actions[action_id] = failed
            self.action_attempts.setdefault(attempt.attempt_id, deepcopy(attempt))
            return True

    async def record_action_attempt(self, attempt: ActionAttempt) -> bool:
        async with self._lock:
            existing = self.action_attempts.get(attempt.attempt_id)
            if existing is not None:
                return existing == attempt
            self.action_attempts[attempt.attempt_id] = deepcopy(attempt)
            return True

    async def list_action_attempts(self, action_id: str) -> list[ActionAttempt]:
        async with self._lock:
            return [
                deepcopy(attempt)
                for attempt in self.action_attempts.values()
                if attempt.action_id == action_id
            ]

    async def mark_action_verified(self, *, action_id: str, verified: bool) -> bool:
        async with self._lock:
            action = self.actions.get(action_id)
            if action is None or action.execution_status != ActionStatus.SUCCEEDED:
                return False
            action = action.model_copy(deep=True)
            action.execution_status = (
                ActionStatus.VERIFIED if verified else ActionStatus.VERIFICATION_FAILED
            )
            self.actions[action_id] = action
            return True

    async def store_approval(self, approval: ApprovalRequest) -> bool:
        async with self._lock:
            existing = self.approvals.get(approval.approval_id)
            if existing is not None:
                return existing.callback_token_hash == approval.callback_token_hash
            self.approvals[approval.approval_id] = approval.model_copy(deep=True)
            return True

    async def get_approval(self, approval_id: str) -> ApprovalRequest | None:
        async with self._lock:
            approval = self.approvals.get(approval_id)
            return approval.model_copy(deep=True) if approval is not None else None

    async def get_approval_by_callback_token_hash(
        self, callback_token_hash: str
    ) -> ApprovalRequest | None:
        async with self._lock:
            for approval in self.approvals.values():
                if approval.callback_token_hash == callback_token_hash:
                    return approval.model_copy(deep=True)
            return None

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
    ) -> bool:
        async with self._lock:
            approval = self.approvals.get(approval_id)
            if approval is None:
                return False
            incident = self.incidents.get(approval.incident_id)
            plan = self.plans.get((approval.incident_id, approval.plan_version))
            trip = self.trips.get(incident.trip_id) if incident is not None else None
            if (
                incident is None
                or trip is None
                or trip.active_incident_id != approval.incident_id
                or incident.status != IncidentStatus.WAITING_APPROVAL
                or plan is None
                or plan.status.value != "CURRENT"
                or plan.plan_hash != approval.plan_hash
                or plan.policy_version != approval.policy_version
                or plan.selected_option.option_fingerprint != approval.option_fingerprint
                or plan.total_incremental_cost != approval.maximum_authorized
                or plan.valid_until <= now
                or approval.status != ApprovalStatus.PENDING
                or approval.callback_token_hash != callback_token_hash
                or approval.telegram_user_id != telegram_user_id
                or approval.telegram_chat_id != telegram_chat_id
                or approval.expires_at <= now
                or outbox.command.incident_id != approval.incident_id
                or outbox.command.plan_version != approval.plan_version
            ):
                return False
            if update_id in self.telegram_updates or outbox.outbox_id in self.outbox:
                return False
            consumed = approval.model_copy(deep=True)
            consumed.status = ApprovalStatus.APPROVED
            consumed.decided_at = now
            consumed.consumed_update_id = update_id
            self.approvals[approval_id] = consumed
            self.telegram_updates[update_id] = canonical_hash(
                {"approval_id": approval_id, "status": "approved"}
            )
            self.outbox[outbox.outbox_id] = outbox.model_copy(deep=True)
            return True

    async def decline_approval(
        self,
        *,
        approval_id: str,
        callback_token_hash: str,
        telegram_user_id: str,
        telegram_chat_id: str,
        update_id: str,
        now: datetime,
    ) -> bool:
        async with self._lock:
            approval = self.approvals.get(approval_id)
            if approval is None:
                return False
            incident = self.incidents.get(approval.incident_id)
            trip = self.trips.get(incident.trip_id) if incident is not None else None
            if (
                incident is None
                or trip is None
                or trip.active_incident_id != approval.incident_id
                or incident.status != IncidentStatus.WAITING_APPROVAL
                or approval.status != ApprovalStatus.PENDING
                or approval.callback_token_hash != callback_token_hash
                or approval.telegram_user_id != telegram_user_id
                or approval.telegram_chat_id != telegram_chat_id
                or approval.expires_at <= now
                or update_id in self.telegram_updates
            ):
                return False
            declined = approval.model_copy(deep=True)
            declined.status = ApprovalStatus.DECLINED
            declined.decided_at = now
            declined.consumed_update_id = update_id
            self.approvals[approval_id] = declined
            cancelled = deepcopy(incident)
            cancelled.status = IncidentStatus.CANCELLED
            cancelled.version += 1
            cancelled.updated_at = now
            self.incidents[incident.incident_id] = cancelled
            self.telegram_updates[update_id] = canonical_hash(
                {"approval_id": approval_id, "status": "declined"}
            )
            return True

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
    ) -> bool:
        async with self._lock:
            approval = self.approvals.get(approval_id)
            if approval is None:
                return False
            incident = self.incidents.get(approval.incident_id)
            trip = self.trips.get(incident.trip_id) if incident is not None else None
            expected_incident = (
                IncidentStatus.CANCELLED if resume_cancelled else IncidentStatus.WAITING_APPROVAL
            )
            expected_approval = (
                ApprovalStatus.DECLINED if resume_cancelled else ApprovalStatus.PENDING
            )
            if (
                incident is None
                or trip is None
                or trip.active_incident_id != approval.incident_id
                or incident.status != expected_incident
                or approval.status != expected_approval
                or approval.callback_token_hash != callback_token_hash
                or approval.telegram_user_id != telegram_user_id
                or approval.telegram_chat_id != telegram_chat_id
                or update_id in self.telegram_updates
                or outbox.outbox_id in self.outbox
                or outbox.command.incident_id != approval.incident_id
                or outbox.command.type != WorkflowCommandType.REPLAN
            ):
                return False
            superseded = approval.model_copy(deep=True)
            superseded.status = ApprovalStatus.SUPERSEDED
            superseded.decided_at = now
            superseded.consumed_update_id = update_id
            replanning = deepcopy(incident)
            replanning.status = IncidentStatus.PLANNING
            replanning.version += 1
            replanning.updated_at = now
            self.approvals[approval_id] = superseded
            self.incidents[incident.incident_id] = replanning
            self.telegram_updates[update_id] = canonical_hash(
                {"approval_id": approval_id, "status": "replan"}
            )
            self.outbox[outbox.outbox_id] = outbox.model_copy(deep=True)
            return True

    async def expire_approval_and_enqueue_replan(
        self,
        *,
        approval_id: str,
        now: datetime,
        outbox: OutboxRecord,
    ) -> bool:
        async with self._lock:
            approval = self.approvals.get(approval_id)
            if approval is None:
                return False
            existing_outbox = self.outbox.get(outbox.outbox_id)
            if approval.status == ApprovalStatus.EXPIRED:
                return (
                    existing_outbox is not None
                    and existing_outbox.command.command_id == outbox.command.command_id
                )
            incident = self.incidents.get(approval.incident_id)
            if (
                incident is None
                or incident.status != IncidentStatus.WAITING_APPROVAL
                or approval.status != ApprovalStatus.PENDING
                or approval.expires_at > now
                or existing_outbox is not None
                or outbox.command.incident_id != approval.incident_id
            ):
                return False
            expired = approval.model_copy(deep=True)
            expired.status = ApprovalStatus.EXPIRED
            expired.decided_at = now
            self.approvals[approval_id] = expired
            replanning = deepcopy(incident)
            replanning.status = IncidentStatus.PLANNING
            replanning.version += 1
            replanning.updated_at = now
            self.incidents[incident.incident_id] = replanning
            self.outbox[outbox.outbox_id] = outbox.model_copy(deep=True)
            return True

    async def claim_telegram_update(self, *, update_id: str, payload_hash: str) -> bool:
        async with self._lock:
            existing = self.telegram_updates.get(update_id)
            if existing is None:
                self.telegram_updates[update_id] = payload_hash
                return True
            if existing != payload_hash:
                raise EventPayloadConflict(
                    f"Telegram update {update_id!r} was reused with new payload"
                )
            return False

    async def claim_telegram_rate_slot(
        self,
        *,
        telegram_user_id: str,
        update_kind: str,
        window_started_at: datetime,
        limit: int,
    ) -> bool:
        async with self._lock:
            key = (telegram_user_id, update_kind, window_started_at)
            count = self.telegram_rate_limits.get(key, 0)
            if count >= limit:
                return False
            self.telegram_rate_limits[key] = count + 1
            return True

    async def enqueue_outbox_once(self, outbox: OutboxRecord) -> bool:
        async with self._lock:
            existing = self.outbox.get(outbox.outbox_id)
            if existing is None:
                self.outbox[outbox.outbox_id] = outbox.model_copy(deep=True)
                return True
            return existing.command.command_id == outbox.command.command_id

    async def get_outbox(self, outbox_id: str) -> OutboxRecord | None:
        async with self._lock:
            record = self.outbox.get(outbox_id)
            return record.model_copy(deep=True) if record is not None else None

    async def list_pending_outbox(self, *, limit: int = 100) -> list[OutboxRecord]:
        async with self._lock:
            pending = [
                record.model_copy(deep=True)
                for record in self.outbox.values()
                if record.status == OutboxStatus.PENDING
            ]
            return sorted(pending, key=lambda record: record.created_at)[:limit]

    async def mark_outbox_published(self, *, outbox_id: str, published_at: datetime) -> bool:
        async with self._lock:
            record = self.outbox.get(outbox_id)
            if record is None:
                return False
            if record.status == OutboxStatus.PUBLISHED:
                return True
            published = record.model_copy(deep=True)
            published.status = OutboxStatus.PUBLISHED
            published.published_at = published_at
            self.outbox[outbox_id] = published
            return True

    async def claim_workflow_command(
        self,
        *,
        command: WorkflowCommand,
        worker_id: str,
        lease_expires_at: datetime,
        now: datetime,
    ) -> bool:
        async with self._lock:
            if command.not_before is not None and command.not_before > now:
                return False
            payload_hash = canonical_hash(command)
            existing = self.workflow_commands.get(command.command_id)
            if existing is not None and existing.payload_hash != payload_hash:
                raise EventPayloadConflict(
                    f"Workflow command {command.command_id!r} was reused with new payload"
                )
            if existing is not None:
                if existing.status == WorkflowCommandStatus.COMPLETED:
                    return False
                if existing.lease_expires_at is not None and existing.lease_expires_at > now:
                    return False
            self.workflow_commands[command.command_id] = WorkflowCommandState(
                command=command,
                payload_hash=payload_hash,
                status=WorkflowCommandStatus.LEASED,
                lease_owner=worker_id,
                lease_expires_at=lease_expires_at,
            )
            return True

    async def complete_workflow_command(
        self, *, command_id: str, worker_id: str, completed_at: datetime
    ) -> bool:
        async with self._lock:
            state = self.workflow_commands.get(command_id)
            if state is None:
                return False
            if state.status == WorkflowCommandStatus.COMPLETED:
                return True
            if state.lease_owner != worker_id:
                return False
            completed = state.model_copy(deep=True)
            completed.status = WorkflowCommandStatus.COMPLETED
            completed.lease_owner = None
            completed.lease_expires_at = None
            completed.completed_at = completed_at
            self.workflow_commands[command_id] = completed
            return True

    async def get_workflow_command_state(self, command_id: str) -> WorkflowCommandState | None:
        async with self._lock:
            state = self.workflow_commands.get(command_id)
            return state.model_copy(deep=True) if state is not None else None

    async def apply_demo_provider_effect(
        self,
        *,
        resource_id: str,
        effect_key: str,
        desired_state: dict[str, object],
    ) -> bool:
        async with self._lock:
            current = self.demo_provider_state.get(resource_id)
            if current is not None and current.get("effect_key") == effect_key:
                return True
            self.demo_provider_state[resource_id] = {
                "effect_key": effect_key,
                "desired_state": deepcopy(desired_state),
            }
            return True

    async def get_demo_provider_state(self, resource_id: str) -> dict[str, object] | None:
        async with self._lock:
            current = self.demo_provider_state.get(resource_id)
            return deepcopy(current) if current is not None else None

    async def get_traveler(self, telegram_user_id: str) -> TravelerProfile | None:
        async with self._lock:
            traveler = self.travelers.get(telegram_user_id)
            return traveler.model_copy(deep=True) if traveler is not None else None

    async def get_traveler_by_user_id(self, user_id: str) -> TravelerProfile | None:
        async with self._lock:
            traveler = next(
                (
                    candidate
                    for candidate in self.travelers.values()
                    if candidate.user_id == user_id
                ),
                None,
            )
            return traveler.model_copy(deep=True) if traveler is not None else None

    async def save_traveler(self, traveler: TravelerProfile) -> None:
        async with self._lock:
            self.travelers[traveler.telegram_user_id] = traveler.model_copy(deep=True)

    async def activate_traveler_policy(
        self, *, traveler: TravelerProfile, policy: AutonomyPolicy
    ) -> bool:
        async with self._lock:
            current = self.travelers.get(traveler.telegram_user_id)
            if current is None or current.user_id != traveler.user_id:
                return False
            key = (policy.user_id, policy.version)
            existing = self.policies.get(key)
            if existing is not None:
                return existing == policy and current.active_policy_version == policy.version
            expected_previous = policy.version - 1 or None
            if current.active_policy_version != expected_previous:
                return False
            self.policies[key] = policy.model_copy(deep=True)
            self.travelers[traveler.telegram_user_id] = traveler.model_copy(deep=True)
            return True

    async def get_traveler_policy(self, *, user_id: str, version: int) -> AutonomyPolicy | None:
        async with self._lock:
            policy = self.policies.get((user_id, version))
            return policy.model_copy(deep=True) if policy is not None else None

    async def get_notification(self, notification_id: str) -> OutboundNotification | None:
        async with self._lock:
            notification = self.notifications.get(notification_id)
            return notification.model_copy(deep=True) if notification is not None else None

    async def store_notification_intent(self, notification: OutboundNotification) -> bool:
        async with self._lock:
            existing = self.notifications.get(notification.notification_id)
            if existing is not None:
                return (
                    existing.view_hash == notification.view_hash
                    and existing.chat_id == notification.chat_id
                )
            self.notifications[notification.notification_id] = notification.model_copy(deep=True)
            return True

    async def mark_notification_sent(
        self, *, notification_id: str, message_id: int, sent_at: datetime
    ) -> bool:
        async with self._lock:
            existing = self.notifications.get(notification_id)
            if existing is None:
                return False
            if existing.status == "SENT":
                return existing.message_id == message_id
            sent = existing.model_copy(deep=True)
            sent.status = "SENT"
            sent.message_id = message_id
            sent.sent_at = sent_at
            self.notifications[notification_id] = sent
            return True

    async def mark_notification_unknown(
        self, *, notification_id: str, unknown_at: datetime
    ) -> bool:
        async with self._lock:
            existing = self.notifications.get(notification_id)
            if existing is None:
                return False
            if existing.status == "SENT":
                return False
            unknown = existing.model_copy(deep=True)
            unknown.status = "UNKNOWN"
            unknown.unknown_at = unknown_at
            self.notifications[notification_id] = unknown
            return True

    async def mark_notification_blocked(
        self, *, notification_id: str, blocked_at: datetime, failure_code: str
    ) -> bool:
        async with self._lock:
            existing = self.notifications.get(notification_id)
            if existing is None or existing.status == "SENT":
                return False
            blocked = existing.model_copy(deep=True)
            blocked.status = "BLOCKED"
            blocked.blocked_at = blocked_at
            blocked.failure_code = failure_code
            self.notifications[notification_id] = blocked
            return True

    async def store_ai_handoff(self, handoff: AiConnectionHandoff) -> bool:
        async with self._lock:
            if handoff.state_hash in self.ai_handoffs:
                return False
            self.ai_handoffs[handoff.state_hash] = handoff.model_copy(deep=True)
            return True

    async def consume_ai_handoff(
        self,
        *,
        state_hash: str,
        telegram_user_id: str,
        telegram_chat_id: str,
        now: datetime,
    ) -> AiConnectionHandoff | None:
        async with self._lock:
            handoff = self.ai_handoffs.get(state_hash)
            if (
                handoff is None
                or handoff.telegram_user_id != telegram_user_id
                or handoff.telegram_chat_id != telegram_chat_id
                or handoff.consumed_at is not None
                or handoff.expires_at <= now
            ):
                return None
            consumed = handoff.model_copy(deep=True)
            consumed.consumed_at = now
            self.ai_handoffs[state_hash] = consumed
            return consumed.model_copy(deep=True)

    async def get_ai_connection(self, telegram_user_id: str) -> AiConnection | None:
        async with self._lock:
            connection = self.ai_connections.get(telegram_user_id)
            return connection.model_copy(deep=True) if connection is not None else None

    async def save_ai_connection(self, connection: AiConnection) -> None:
        async with self._lock:
            self.ai_connections[connection.telegram_user_id] = connection.model_copy(deep=True)

    async def store_calendar_oauth_state(self, state: CalendarOAuthState) -> bool:
        async with self._lock:
            if state.state_hash in self.calendar_oauth_states:
                return False
            self.calendar_oauth_states[state.state_hash] = state.model_copy(deep=True)
            return True

    async def get_calendar_oauth_state(self, state_hash: str) -> CalendarOAuthState | None:
        async with self._lock:
            state = self.calendar_oauth_states.get(state_hash)
            return state.model_copy(deep=True) if state is not None else None

    async def consume_calendar_oauth_state(
        self,
        *,
        state_hash: str,
        telegram_user_id: str,
        telegram_chat_id: str,
        redirect_uri: str,
        code_verifier_hash: str,
        now: datetime,
    ) -> CalendarOAuthState | None:
        async with self._lock:
            state = self.calendar_oauth_states.get(state_hash)
            if (
                state is None
                or state.telegram_user_id != telegram_user_id
                or state.telegram_chat_id != telegram_chat_id
                or state.redirect_uri != redirect_uri
                or state.code_verifier_hash != code_verifier_hash
                or state.consumed_at is not None
                or state.expires_at <= now
            ):
                return None
            consumed = state.model_copy(deep=True)
            consumed.consumed_at = now
            self.calendar_oauth_states[state_hash] = consumed
            return consumed

    async def get_calendar_connection(self, telegram_user_id: str) -> CalendarConnection | None:
        async with self._lock:
            connection = self.calendar_connections.get(telegram_user_id)
            return connection.model_copy(deep=True) if connection is not None else None

    async def save_calendar_connection(self, connection: CalendarConnection) -> None:
        async with self._lock:
            self.calendar_connections[connection.telegram_user_id] = connection.model_copy(
                deep=True
            )

    async def store_gmail_oauth_state(self, state: GmailOAuthState) -> bool:
        async with self._lock:
            if state.state_hash in self.gmail_oauth_states:
                return False
            self.gmail_oauth_states[state.state_hash] = state.model_copy(deep=True)
            return True

    async def get_gmail_oauth_state(self, state_hash: str) -> GmailOAuthState | None:
        async with self._lock:
            state = self.gmail_oauth_states.get(state_hash)
            return state.model_copy(deep=True) if state is not None else None

    async def consume_gmail_oauth_state(
        self,
        *,
        state_hash: str,
        telegram_user_id: str,
        telegram_chat_id: str,
        redirect_uri: str,
        code_verifier_hash: str,
        now: datetime,
    ) -> GmailOAuthState | None:
        async with self._lock:
            state = self.gmail_oauth_states.get(state_hash)
            if (
                state is None
                or state.telegram_user_id != telegram_user_id
                or state.telegram_chat_id != telegram_chat_id
                or state.redirect_uri != redirect_uri
                or state.code_verifier_hash != code_verifier_hash
                or state.consumed_at is not None
                or state.expires_at <= now
            ):
                return None
            consumed = state.model_copy(deep=True)
            consumed.consumed_at = now
            self.gmail_oauth_states[state_hash] = consumed
            return consumed

    async def get_gmail_connection(self, telegram_user_id: str) -> GmailConnection | None:
        async with self._lock:
            connection = self.gmail_connections.get(telegram_user_id)
            return connection.model_copy(deep=True) if connection is not None else None

    async def save_gmail_connection(self, connection: GmailConnection) -> None:
        async with self._lock:
            self.gmail_connections[connection.telegram_user_id] = connection.model_copy(deep=True)

    async def save_expense_once(self, expense: TripExpense) -> bool:
        async with self._lock:
            existing = self.expenses.get(expense.expense_id)
            if existing is not None:
                return existing == expense
            self.expenses[expense.expense_id] = expense.model_copy(deep=True)
            return True

    async def get_expense(self, expense_id: str) -> TripExpense | None:
        async with self._lock:
            expense = self.expenses.get(expense_id)
            return expense.model_copy(deep=True) if expense is not None else None

    async def list_expenses(self, trip_id: str) -> list[TripExpense]:
        async with self._lock:
            return [
                expense.model_copy(deep=True)
                for expense in self.expenses.values()
                if expense.trip_id == trip_id
            ]

    async def save_trip_document_once(self, document: TripDocument) -> bool:
        async with self._lock:
            existing = self.trip_documents.get(document.document_id)
            if existing is not None:
                return existing == document
            self.trip_documents[document.document_id] = document.model_copy(deep=True)
            return True

    async def list_trip_documents(self, trip_id: str) -> list[TripDocument]:
        async with self._lock:
            return [
                document.model_copy(deep=True)
                for document in self.trip_documents.values()
                if document.trip_id == trip_id
            ]

    async def save_financial_item_once(self, item: OpenFinancialItem) -> bool:
        async with self._lock:
            existing = self.financial_items.get(item.financial_item_id)
            if existing is not None:
                return existing == item
            self.financial_items[item.financial_item_id] = item.model_copy(deep=True)
            return True

    async def list_financial_items(self, trip_id: str) -> list[OpenFinancialItem]:
        async with self._lock:
            return [
                item.model_copy(deep=True)
                for item in self.financial_items.values()
                if item.trip_id == trip_id
            ]

    async def settle_financial_item(
        self,
        *,
        financial_item_id: str,
        owner_user_id: str,
        actual_amount: Money,
        settled_at: datetime,
    ) -> OpenFinancialItem | None:
        async with self._lock:
            item = self.financial_items.get(financial_item_id)
            if item is None or item.owner_user_id != owner_user_id:
                return None
            if item.status == "SETTLED":
                return item.model_copy(deep=True) if item.actual_amount == actual_amount else None
            if item.expected_amount != actual_amount:
                return None
            settled = item.model_copy(deep=True)
            settled.status = "SETTLED"
            settled.actual_amount = actual_amount
            settled.settled_at = settled_at
            settled.updated_at = settled_at
            self.financial_items[financial_item_id] = settled
            return settled.model_copy(deep=True)

    async def set_trip_status(
        self,
        *,
        trip_id: str,
        owner_user_id: str,
        status: TripStatus,
        updated_at: datetime,
    ) -> bool:
        async with self._lock:
            trip = self.trips.get(trip_id)
            if trip is None or trip.owner_user_id != owner_user_id:
                return False
            updated = trip.model_copy(deep=True)
            updated.status = status
            updated.updated_at = updated_at
            self.trips[trip_id] = updated
            return True


class LocalEventPublisher:
    def __init__(self) -> None:
        self.events: list[DisruptionEvent] = []
        self.commands: list[WorkflowCommand] = []

    async def publish(self, event: DisruptionEvent) -> str:
        self.events.append(event.model_copy(deep=True))
        return f"local-{len(self.events)}"

    async def publish_command(self, command: WorkflowCommand) -> str:
        self.commands.append(command.model_copy(deep=True))
        return f"local-command-{len(self.commands)}"
