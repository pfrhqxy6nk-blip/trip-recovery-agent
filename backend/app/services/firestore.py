from __future__ import annotations

import asyncio
from datetime import date, datetime
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


async def _delete_refs(client: Any, refs: list[Any]) -> None:
    """Delete references in bounded batches (Firestore accepts at most 500 writes)."""

    for offset in range(0, len(refs), 400):
        batch = client.batch()
        for ref in refs[offset : offset + 400]:
            batch.delete(ref)
        await batch.commit()


async def _nested_refs(parent: Any, collection_names: tuple[str, ...]) -> list[Any]:
    refs: list[Any] = []
    for collection_name in collection_names:
        async for snapshot in parent.collection(collection_name).stream():
            refs.append(snapshot.reference)
            if collection_name == "actions":
                async for attempt in snapshot.reference.collection("attempts").stream():
                    refs.append(attempt.reference)
    return refs


def _firestore_trip_payload(value: Any) -> Any:
    """Make Pydantic trip data compatible with Firestore's value encoder.

    Firestore supports timestamps (``datetime``) but not Python ``date``
    instances.  Keep timestamps as timestamps for range queries and encode
    date-only fields (for example a flight's local scheduled date) as ISO
    strings that Pydantic restores on read.
    """

    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _firestore_trip_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_firestore_trip_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_firestore_trip_payload(item) for item in value)
    return value


class FirestoreIncidentRepository:
    def __init__(self, project_id: str) -> None:
        from google.cloud import firestore_v1

        self._firestore = firestore_v1
        self._client = firestore_v1.AsyncClient(project=project_id)

    async def seed_trip(self, trip: Trip) -> None:
        trip_ref = self._client.collection("trips").document(trip.trip_id)
        header = _firestore_trip_payload(
            trip.model_dump(mode="python", exclude={"items", "dependencies"})
        )
        batch = self._client.batch()
        batch.set(trip_ref, header, merge=True)
        for item in trip.items:
            batch.set(
                trip_ref.collection("items").document(item.item_id),
                _firestore_trip_payload(item.model_dump(mode="json")),
                merge=True,
            )
        for dependency in trip.dependencies:
            batch.set(
                trip_ref.collection("dependencies").document(dependency.dependency_id),
                _firestore_trip_payload(dependency.model_dump(mode="json")),
                merge=True,
            )
        await batch.commit()

    async def get_trip(self, trip_id: str) -> Trip | None:
        trip_ref = self._client.collection("trips").document(trip_id)
        snapshot = await trip_ref.get()
        if not snapshot.exists:
            return None

        items = [doc.to_dict() async for doc in trip_ref.collection("items").stream()]
        dependencies = [doc.to_dict() async for doc in trip_ref.collection("dependencies").stream()]
        payload = snapshot.to_dict() or {}
        payload["items"] = items
        payload["dependencies"] = dependencies
        return Trip.model_validate(payload)

    async def list_trips_for_owner(self, owner_user_id: str) -> list[Trip]:
        query = self._client.collection("trips").where("owner_user_id", "==", owner_user_id)
        trip_ids = [document.id async for document in query.stream()]
        trips = await asyncio.gather(*(self.get_trip(trip_id) for trip_id in trip_ids))
        return [trip for trip in trips if trip is not None]

    async def delete_traveler_data(self, telegram_user_id: str) -> list[str]:
        """Delete one traveler's trip graph, workflow history and OAuth metadata."""

        traveler_ref = self._client.collection("telegramUsers").document(telegram_user_id)
        traveler_snapshot = await traveler_ref.get()
        if not traveler_snapshot.exists:
            return []
        traveler_payload = traveler_snapshot.to_dict() or {}
        owner_user_id = str(traveler_payload.get("user_id", ""))
        refs: list[Any] = [traveler_ref]
        secret_resources: list[str] = []

        for connection_name in ("gemini", "calendar", "gmail"):
            connection_ref = traveler_ref.collection("connections").document(connection_name)
            connection_snapshot = await connection_ref.get()
            if connection_snapshot.exists:
                resource_name = (connection_snapshot.to_dict() or {}).get("secret_resource_name")
                if isinstance(resource_name, str) and resource_name:
                    secret_resources.append(resource_name)
                refs.append(connection_ref)

        # A malformed legacy profile must never turn a delete request into a
        # query for every record whose owner field is empty. Remove only the
        # Telegram-scoped records in that case and leave the rest for repair.
        if not owner_user_id:
            refs.append(self._client.collection("tripDrafts").document(telegram_user_id))
            for collection_name in (
                "calendarOAuthStates",
                "gmailOAuthStates",
                "aiConnectionStates",
            ):
                async for snapshot in (
                    self._client.collection(collection_name)
                    .where("telegram_user_id", "==", telegram_user_id)
                    .stream()
                ):
                    refs.append(snapshot.reference)
            await _delete_refs(self._client, refs)
            return secret_resources

        policy_parent = self._client.collection("travelers").document(owner_user_id)
        refs.append(policy_parent)
        async for policy in policy_parent.collection("policies").stream():
            refs.append(policy.reference)

        draft_ref = self._client.collection("tripDrafts").document(telegram_user_id)
        refs.append(draft_ref)
        for collection_name in ("calendarOAuthStates", "gmailOAuthStates", "aiConnectionStates"):
            async for snapshot in (
                self._client.collection(collection_name)
                .where("telegram_user_id", "==", telegram_user_id)
                .stream()
            ):
                refs.append(snapshot.reference)

        trip_ids = [
            snapshot.id
            async for snapshot in self._client.collection("trips")
            .where("owner_user_id", "==", owner_user_id)
            .stream()
        ]
        incident_ids: list[str] = []
        watchpoint_ids: list[str] = []
        for trip_id in trip_ids:
            trip_ref = self._client.collection("trips").document(trip_id)
            refs.append(trip_ref)
            refs.extend(await _nested_refs(trip_ref, ("items", "dependencies")))
            for collection_name in (
                "monitoringSubscriptions",
                "watchpoints",
                "tripDocuments",
                "tripExpenses",
                "tripFinancialItems",
            ):
                async for snapshot in (
                    self._client.collection(collection_name)
                    .where("trip_id", "==", trip_id)
                    .stream()
                ):
                    refs.append(snapshot.reference)
                    if collection_name == "watchpoints":
                        watchpoint_ids.append(snapshot.id)
            async for snapshot in (
                self._client.collection("incidents").where("trip_id", "==", trip_id).stream()
            ):
                incident_ids.append(snapshot.id)
                refs.append(snapshot.reference)
                refs.extend(
                    await _nested_refs(snapshot.reference, ("plans", "actions", "approvals"))
                )

        for incident_id in incident_ids:
            for collection_name in (
                "processedEvents",
                "notifications",
                "outbox",
                "workflowCommands",
            ):
                async for snapshot in (
                    self._client.collection(collection_name)
                    .where("incident_id", "==", incident_id)
                    .stream()
                ):
                    refs.append(snapshot.reference)
        for watchpoint_id in watchpoint_ids:
            async for snapshot in (
                self._client.collection("groundedSignals")
                .where("watchpoint_id", "==", watchpoint_id)
                .stream()
            ):
                refs.append(snapshot.reference)

        await _delete_refs(self._client, refs)
        return secret_resources

    async def create_trip_once(self, trip: Trip) -> bool:
        trip_ref = self._client.collection("trips").document(trip.trip_id)
        transaction = self._client.transaction()
        header = _firestore_trip_payload(
            trip.model_dump(mode="python", exclude={"items", "dependencies"})
        )

        @self._firestore.async_transactional
        async def create(transaction: Any) -> bool:
            snapshot = await trip_ref.get(transaction=transaction)
            if snapshot.exists:
                existing = snapshot.to_dict() or {}
                if (
                    existing.get("owner_user_id") != trip.owner_user_id
                    or existing.get("intake_hash") != trip.intake_hash
                ):
                    raise TripCreateConflict("trip ID is already bound to different intake data")
                return False
            transaction.create(trip_ref, header)
            for item in trip.items:
                transaction.create(
                    trip_ref.collection("items").document(item.item_id),
                    _firestore_trip_payload(item.model_dump(mode="json")),
                )
            for dependency in trip.dependencies:
                transaction.create(
                    trip_ref.collection("dependencies").document(dependency.dependency_id),
                    _firestore_trip_payload(dependency.model_dump(mode="json")),
                )
            return True

        return await create(transaction)

    async def put_monitoring_subscription(self, subscription: MonitoringSubscription) -> bool:
        ref = self._client.collection("monitoringSubscriptions").document(
            subscription.subscription_id
        )
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def put(transaction: Any) -> bool:
            snapshot = await ref.get(transaction=transaction)
            if not snapshot.exists:
                transaction.create(ref, subscription.model_dump(mode="json"))
                return True
            return MonitoringSubscription.model_validate(snapshot.to_dict()) == subscription

        return await put(transaction)

    async def get_monitoring_subscription(
        self, subscription_id: str
    ) -> MonitoringSubscription | None:
        snapshot = (
            await self._client.collection("monitoringSubscriptions").document(subscription_id).get()
        )
        return (
            MonitoringSubscription.model_validate(snapshot.to_dict()) if snapshot.exists else None
        )

    async def list_monitoring_subscriptions(self, trip_id: str) -> list[MonitoringSubscription]:
        query = self._client.collection("monitoringSubscriptions").where("trip_id", "==", trip_id)
        return [
            MonitoringSubscription.model_validate(doc.to_dict()) async for doc in query.stream()
        ]

    async def update_monitoring_subscription(
        self, subscription: MonitoringSubscription, *, expected_fingerprint: str | None
    ) -> bool:
        ref = self._client.collection("monitoringSubscriptions").document(
            subscription.subscription_id
        )
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def update(transaction: Any) -> bool:
            snapshot = await ref.get(transaction=transaction)
            if not snapshot.exists:
                return False
            current = MonitoringSubscription.model_validate(snapshot.to_dict())
            if current.last_snapshot_fingerprint != expected_fingerprint:
                return False
            transaction.set(ref, subscription.model_dump(mode="json"))
            return True

        return await update(transaction)

    async def put_watchpoint(self, watchpoint: TripWatchpoint) -> bool:
        ref = self._client.collection("watchpoints").document(watchpoint.watchpoint_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def put(transaction: Any) -> bool:
            snapshot = await ref.get(transaction=transaction)
            if not snapshot.exists:
                transaction.create(ref, watchpoint.model_dump(mode="json"))
                return True
            return TripWatchpoint.model_validate(snapshot.to_dict()) == watchpoint

        return await put(transaction)

    async def list_watchpoints(self, trip_id: str) -> list[TripWatchpoint]:
        query = self._client.collection("watchpoints").where("trip_id", "==", trip_id)
        return [TripWatchpoint.model_validate(doc.to_dict()) async for doc in query.stream()]

    async def get_watchpoint(self, watchpoint_id: str) -> TripWatchpoint | None:
        snapshot = await self._client.collection("watchpoints").document(watchpoint_id).get()
        if not snapshot.exists:
            return None
        return TripWatchpoint.model_validate(snapshot.to_dict())

    async def list_due_watchpoints(self, now: datetime, *, limit: int) -> list[TripWatchpoint]:
        query = (
            self._client.collection("watchpoints")
            .where("due_at", "<=", now)
            .order_by("due_at")
            .limit(limit)
        )
        return [TripWatchpoint.model_validate(doc.to_dict()) async for doc in query.stream()]

    async def reschedule_watchpoint(
        self, watchpoint: TripWatchpoint, *, expected_due_at: datetime
    ) -> bool:
        ref = self._client.collection("watchpoints").document(watchpoint.watchpoint_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def reschedule(transaction: Any) -> bool:
            snapshot = await ref.get(transaction=transaction)
            if not snapshot.exists:
                return False
            current = TripWatchpoint.model_validate(snapshot.to_dict())
            if current.due_at != expected_due_at:
                return False
            transaction.set(ref, watchpoint.model_dump(mode="json"))
            return True

        return await reschedule(transaction)

    async def put_grounded_signal(self, signal: GroundedTravelSignal) -> bool:
        ref = self._client.collection("groundedSignals").document(grounded_signal_hash(signal))
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def put(transaction: Any) -> bool:
            snapshot = await ref.get(transaction=transaction)
            if snapshot.exists:
                return False
            transaction.create(ref, signal.model_dump(mode="json"))
            return True

        return await put(transaction)

    async def list_unpublished_grounded_signals(self, *, limit: int) -> list[GroundedTravelSignal]:
        signals: list[GroundedTravelSignal] = []
        async for snapshot in self._client.collection("groundedSignals").stream():
            signal = GroundedTravelSignal.model_validate(snapshot.to_dict())
            if signal.published_at is None:
                signals.append(signal)
        signals.sort(key=lambda value: value.observed_at)
        return signals[:limit]

    async def mark_grounded_signal_published(
        self, *, signal: GroundedTravelSignal, published_at: datetime
    ) -> bool:
        ref = self._client.collection("groundedSignals").document(grounded_signal_hash(signal))
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def mark(transaction: Any) -> bool:
            snapshot = await ref.get(transaction=transaction)
            if not snapshot.exists:
                return False
            current = GroundedTravelSignal.model_validate(snapshot.to_dict())
            if current.published_at is not None:
                return True
            transaction.update(ref, {"published_at": published_at})
            return True

        return await mark(transaction)

    async def get_trip_draft(self, telegram_user_id: str) -> TripDraft | None:
        snapshot = await self._client.collection("tripDrafts").document(telegram_user_id).get()
        if not snapshot.exists:
            return None
        return TripDraft.model_validate(snapshot.to_dict())

    async def save_trip_draft(
        self, *, draft: TripDraft, expected_version: int | None
    ) -> TripDraft | None:
        draft_ref = self._client.collection("tripDrafts").document(draft.telegram_user_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def save(transaction: Any) -> TripDraft | None:
            snapshot = await draft_ref.get(transaction=transaction)
            if not snapshot.exists:
                if expected_version is not None:
                    return None
                stored = draft.model_copy(update={"version": 1})
                transaction.create(draft_ref, stored.model_dump(mode="json"))
                return stored
            current = TripDraft.model_validate(snapshot.to_dict())
            if (
                expected_version != current.version
                or current.owner_user_id != draft.owner_user_id
                or current.telegram_chat_id != draft.telegram_chat_id
            ):
                return None
            stored = draft.model_copy(update={"version": current.version + 1})
            transaction.set(draft_ref, stored.model_dump(mode="json"))
            return stored

        return await save(transaction)

    async def clear_trip_draft(
        self,
        *,
        telegram_user_id: str,
        telegram_chat_id: str,
        expected_version: int,
    ) -> bool:
        draft_ref = self._client.collection("tripDrafts").document(telegram_user_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def clear(transaction: Any) -> bool:
            snapshot = await draft_ref.get(transaction=transaction)
            if not snapshot.exists:
                return False
            current = TripDraft.model_validate(snapshot.to_dict())
            if current.telegram_chat_id != telegram_chat_id or current.version != expected_version:
                return False
            transaction.delete(draft_ref)
            return True

        return await clear(transaction)

    async def claim_event(
        self,
        *,
        event: DisruptionEvent,
        incident: Incident,
        worker_id: str,
        lease_expires_at: datetime,
    ) -> ClaimResult:
        event_ref = self._client.collection("processedEvents").document(event.event_id)
        incident_ref = self._client.collection("incidents").document(incident.incident_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def claim(transaction: Any) -> ClaimResult:
            event_snapshot = await event_ref.get(transaction=transaction)
            payload_hash = canonical_hash(event)
            if not event_snapshot.exists:
                trip_ref = self._client.collection("trips").document(event.trip_id)
                trip_snapshot = await trip_ref.get(transaction=transaction)
                if not trip_snapshot.exists:
                    raise KeyError(f"trip {event.trip_id!r} does not exist")
                transaction.create(
                    event_ref,
                    {
                        "event_id": event.event_id,
                        "incident_id": incident.incident_id,
                        "status": EventProcessingStatus.PROCESSING.value,
                        "lease_owner": worker_id,
                        "lease_expires_at": lease_expires_at,
                        "attempts": 1,
                        "claimed_at": incident.updated_at,
                        "payload_hash": payload_hash,
                    },
                )
                transaction.create(incident_ref, incident.model_dump(mode="json"))
                transaction.update(
                    trip_ref,
                    {
                        "active_incident_id": incident.incident_id,
                        "updated_at": incident.updated_at,
                    },
                )
                return ClaimResult(ClaimKind.NEW, incident.incident_id)

            record = event_snapshot.to_dict() or {}
            existing_payload_hash = record.get("payload_hash")
            if existing_payload_hash is not None and existing_payload_hash != payload_hash:
                raise EventPayloadConflict(
                    f"event ID {event.event_id!r} was reused with a new payload"
                )
            incident_id = str(record["incident_id"])
            status = str(record["status"])
            if status == EventProcessingStatus.COMPLETED.value:
                return ClaimResult(ClaimKind.COMPLETED, incident_id)

            current_lease = record.get("lease_expires_at")
            if (
                status == EventProcessingStatus.PROCESSING.value
                and current_lease is not None
                and current_lease > incident.updated_at
            ):
                return ClaimResult(ClaimKind.IN_PROGRESS, incident_id)

            existing_ref = self._client.collection("incidents").document(incident_id)
            existing_snapshot = await existing_ref.get(transaction=transaction)
            existing = Incident.model_validate(existing_snapshot.to_dict())
            existing.status = IncidentStatus.RECEIVED
            existing.last_error = None
            existing.retry_count += 1
            existing.lease_owner = worker_id
            existing.lease_expires_at = lease_expires_at
            existing.updated_at = incident.updated_at
            existing.version += 1
            transaction.update(
                event_ref,
                {
                    "status": EventProcessingStatus.PROCESSING.value,
                    "lease_owner": worker_id,
                    "lease_expires_at": lease_expires_at,
                    "attempts": int(record.get("attempts", 1)) + 1,
                },
            )
            transaction.set(existing_ref, existing.model_dump(mode="json"))
            return ClaimResult(ClaimKind.RESUMED, incident_id)

        return await claim(transaction)

    async def get_incident(self, incident_id: str) -> Incident | None:
        snapshot = await self._client.collection("incidents").document(incident_id).get()
        if not snapshot.exists:
            return None
        return Incident.model_validate(snapshot.to_dict())

    async def mark_event_retryable(
        self, event_id: str, incident: Incident, error: str, failed_at: datetime
    ) -> bool:
        event_ref = self._client.collection("processedEvents").document(event_id)
        incident_ref = self._client.collection("incidents").document(incident.incident_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def mark(transaction: Any) -> bool:
            snapshot = await incident_ref.get(transaction=transaction)
            if not snapshot.exists:
                return False
            current = Incident.model_validate(snapshot.to_dict())
            if current.version != incident.version:
                return False
            incident.version += 1
            incident.updated_at = failed_at
            transaction.update(
                event_ref,
                {
                    "status": EventProcessingStatus.RETRYABLE.value,
                    "last_error": error,
                    "failed_at": failed_at,
                    "lease_owner": None,
                    "lease_expires_at": None,
                },
            )
            transaction.set(incident_ref, incident.model_dump(mode="json"))
            return True

        return await mark(transaction)

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
        incident_ref = self._client.collection("incidents").document(incident_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def commit(transaction: Any) -> Incident | None:
            snapshot = await incident_ref.get(transaction=transaction)
            if not snapshot.exists:
                return None
            incident = Incident.model_validate(snapshot.to_dict())
            if incident.version != expected_version or incident.status != IncidentStatus.ANALYZING:
                return None
            incident.deterministic_impact = impact
            incident.gemini_model_id = gemini_model_id
            incident.prompt_version = prompt_version
            incident.version += 1
            incident.updated_at = updated_at
            transaction.set(incident_ref, incident.model_dump(mode="json"))
            return incident

        return await commit(transaction)

    async def complete_analysis(
        self,
        *,
        event_id: str,
        incident_id: str,
        expected_version: int,
        interpretation: TravelInterpretation,
        completed_at: datetime,
    ) -> Incident | None:
        incident_ref = self._client.collection("incidents").document(incident_id)
        event_ref = self._client.collection("processedEvents").document(event_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def complete(transaction: Any) -> Incident | None:
            incident_snapshot = await incident_ref.get(transaction=transaction)
            event_snapshot = await event_ref.get(transaction=transaction)
            if not incident_snapshot.exists or not event_snapshot.exists:
                return None
            incident = Incident.model_validate(incident_snapshot.to_dict())
            event_payload = event_snapshot.to_dict() or {}
            if (
                incident.version != expected_version
                or incident.status != IncidentStatus.ANALYZING
                or event_payload.get("incident_id") != incident_id
            ):
                return None
            incident.interpretation = interpretation
            incident.status = IncidentStatus.PLANNING
            incident.analysis_completed_at = completed_at
            incident.last_error = None
            incident.lease_owner = None
            incident.lease_expires_at = None
            incident.version += 1
            incident.updated_at = completed_at
            transaction.set(incident_ref, incident.model_dump(mode="json"))
            transaction.update(
                event_ref,
                {
                    "status": EventProcessingStatus.COMPLETED.value,
                    "completed_at": completed_at,
                    "lease_owner": None,
                    "lease_expires_at": None,
                },
            )
            return incident

        return await complete(transaction)

    async def transition_incident(
        self,
        *,
        incident_id: str,
        expected_version: int,
        from_states: set[IncidentStatus],
        to_state: IncidentStatus,
        updated_at: datetime,
    ) -> Incident | None:
        incident_ref = self._client.collection("incidents").document(incident_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def transition(transaction: Any) -> Incident | None:
            snapshot = await incident_ref.get(transaction=transaction)
            if not snapshot.exists:
                return None
            incident = Incident.model_validate(snapshot.to_dict())
            if incident.version != expected_version or incident.status not in from_states:
                return None
            incident.status = to_state
            if to_state == IncidentStatus.ANALYZING:
                incident.analysis_started_at = updated_at
            incident.version += 1
            incident.updated_at = updated_at
            transaction.set(incident_ref, incident.model_dump(mode="json"))
            return incident

        return await transition(transaction)

    async def commit_plan(self, *, plan: RecoveryPlan, expected_incident_version: int) -> bool:
        incident_ref = self._client.collection("incidents").document(plan.incident_id)
        plan_ref = incident_ref.collection("plans").document(str(plan.version))
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def commit(transaction: Any) -> bool:
            incident_snapshot = await incident_ref.get(transaction=transaction)
            if not incident_snapshot.exists:
                return False
            incident = Incident.model_validate(incident_snapshot.to_dict())
            if incident.version != expected_incident_version:
                return False
            existing = await plan_ref.get(transaction=transaction)
            if existing.exists:
                return (existing.to_dict() or {}).get("plan_hash") == plan.plan_hash
            previous_ref = None
            previous_plan = None
            if plan.version > 1:
                previous_ref = incident_ref.collection("plans").document(str(plan.version - 1))
                previous_snapshot = await previous_ref.get(transaction=transaction)
                if previous_snapshot.exists:
                    previous_plan = RecoveryPlan.model_validate(previous_snapshot.to_dict())
            if (
                previous_ref is not None
                and previous_plan is not None
                and previous_plan.status == PlanStatus.CURRENT
            ):
                previous_plan.status = PlanStatus.SUPERSEDED
                transaction.set(previous_ref, previous_plan.model_dump(mode="json"))
            transaction.create(plan_ref, plan.model_dump(mode="json"))
            incident.version += 1
            transaction.set(incident_ref, incident.model_dump(mode="json"))
            return True

        return await commit(transaction)

    async def get_current_plan(self, incident_id: str) -> RecoveryPlan | None:
        # Keep this query index-free.  A compound ``status + version`` index is
        # easy to miss during a rollout and used to turn the Telegram approval
        # callback into an HTTP 500 on a fresh Firestore database.  Each incident
        # has only a handful of plan versions, so selecting the newest CURRENT
        # version in application code is both bounded and more portable.
        plans = [
            RecoveryPlan.model_validate(snapshot.to_dict())
            async for snapshot in self._client.collection("incidents")
            .document(incident_id)
            .collection("plans")
            .stream()
        ]
        current = [plan for plan in plans if plan.status == PlanStatus.CURRENT]
        return max(current, key=lambda plan: plan.version, default=None)

    async def put_action(self, action: PlannedAction) -> bool:
        action_ref = (
            self._client.collection("incidents")
            .document(action.incident_id)
            .collection("actions")
            .document(action.action_id)
        )
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def put(transaction: Any) -> bool:
            existing = await action_ref.get(transaction=transaction)
            if existing.exists:
                return (existing.to_dict() or {}).get("effect_key") == action.effect_key
            transaction.create(action_ref, action.model_dump(mode="json"))
            return True

        return await put(transaction)

    async def claim_action(
        self,
        *,
        action_id: str,
        worker_id: str,
        lease_expires_at: datetime,
        now: datetime,
    ) -> PlannedAction | None:
        collection = self._client.collection_group("actions")
        snapshots = [
            snapshot
            async for snapshot in collection.where("action_id", "==", action_id).limit(1).stream()
        ]
        if not snapshots:
            return None
        action_ref = snapshots[0].reference
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def claim(transaction: Any) -> PlannedAction | None:
            snapshot = await action_ref.get(transaction=transaction)  # type: ignore[misc]
            if not snapshot.exists:
                return None
            action = PlannedAction.model_validate(snapshot.to_dict())
            reclaimable = (
                action.execution_status == ActionStatus.LEASED
                and action.lease_expires_at is not None
                and action.lease_expires_at <= now
            )
            retryable = action.execution_status == ActionStatus.FAILED_RETRYABLE and (
                action.retry_after is None or action.retry_after <= now
            )
            if (
                action.execution_status != ActionStatus.PENDING
                and not reclaimable
                and not retryable
            ):
                return None
            action.execution_status = ActionStatus.LEASED
            action.lease_owner = worker_id
            action.lease_started_at = now
            action.lease_expires_at = lease_expires_at
            action.retry_after = None
            action.attempt_count += 1
            transaction.set(action_ref, action.model_dump(mode="json"))
            return action

        return await claim(transaction)

    async def get_action(self, action_id: str) -> PlannedAction | None:
        snapshots = [
            snapshot
            async for snapshot in self._client.collection_group("actions")
            .where("action_id", "==", action_id)
            .limit(1)
            .stream()
        ]
        if not snapshots:
            return None
        return PlannedAction.model_validate(snapshots[0].to_dict())

    async def list_actions(self, incident_id: str) -> list[PlannedAction]:
        snapshots = [
            snapshot
            async for snapshot in self._client.collection("incidents")
            .document(incident_id)
            .collection("actions")
            .stream()
        ]
        return [PlannedAction.model_validate(snapshot.to_dict()) for snapshot in snapshots]

    async def complete_action_and_create_effect_receipt(
        self,
        *,
        action_id: str,
        effect_key: str,
        provider_reference: str,
        completed_at: datetime,
        attempt: ActionAttempt | None = None,
    ) -> bool:
        snapshots = [
            snapshot
            async for snapshot in self._client.collection_group("actions")
            .where("action_id", "==", action_id)
            .limit(1)
            .stream()
        ]
        if not snapshots:
            return False
        action_ref = snapshots[0].reference
        effect_ref = self._client.collection("effects").document(canonical_hash(effect_key))
        attempt_ref = (
            action_ref.collection("attempts").document(attempt.attempt_id)
            if attempt is not None
            else None
        )
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def complete(transaction: Any) -> bool:
            action_snapshot = await action_ref.get(transaction=transaction)  # type: ignore[misc]
            if not action_snapshot.exists:
                return False
            action = PlannedAction.model_validate(action_snapshot.to_dict())
            if action.effect_key != effect_key:
                return False
            existing_effect = await effect_ref.get(transaction=transaction)
            existing_attempt = (
                await attempt_ref.get(transaction=transaction) if attempt_ref is not None else None
            )
            if existing_attempt is not None and existing_attempt.exists:
                if ActionAttempt.model_validate(existing_attempt.to_dict()) != attempt:
                    return False
            if existing_effect.exists:
                if action.execution_status != ActionStatus.LEASED:
                    return action.execution_status in {
                        ActionStatus.SUCCEEDED,
                        ActionStatus.VERIFIED,
                    }
                effect_payload = existing_effect.to_dict() or {}
                action.execution_status = ActionStatus.SUCCEEDED
                action.provider_reference = str(effect_payload.get("provider_reference", ""))
                action.lease_owner = None
                action.lease_started_at = None
                action.lease_expires_at = None
                transaction.set(action_ref, action.model_dump(mode="json"))
                if attempt_ref is not None and existing_attempt is not None:
                    if not existing_attempt.exists:
                        assert attempt is not None
                        transaction.create(attempt_ref, attempt.model_dump(mode="json"))
                return True
            if action.execution_status != ActionStatus.LEASED:
                return False
            action.execution_status = ActionStatus.SUCCEEDED
            action.provider_reference = provider_reference
            action.lease_owner = None
            action.lease_started_at = None
            action.lease_expires_at = None
            transaction.set(action_ref, action.model_dump(mode="json"))
            transaction.create(
                effect_ref,
                {
                    "action_id": action_id,
                    "provider_reference": provider_reference,
                    "completed_at": completed_at,
                },
            )
            if attempt_ref is not None and existing_attempt is not None:
                if not existing_attempt.exists:
                    assert attempt is not None
                    transaction.create(attempt_ref, attempt.model_dump(mode="json"))
            return True

        return await complete(transaction)

    async def fail_action(
        self,
        *,
        action_id: str,
        worker_id: str,
        retry_after: datetime | None,
        attempt: ActionAttempt,
    ) -> bool:
        snapshots = [
            snapshot
            async for snapshot in self._client.collection_group("actions")
            .where("action_id", "==", action_id)
            .limit(1)
            .stream()
        ]
        if not snapshots:
            return False
        action_ref = snapshots[0].reference
        attempt_ref = action_ref.collection("attempts").document(attempt.attempt_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def fail(transaction: Any) -> bool:
            action_snapshot = await action_ref.get(transaction=transaction)  # type: ignore[misc]
            attempt_snapshot = await attempt_ref.get(transaction=transaction)
            if not action_snapshot.exists:
                return False
            action = PlannedAction.model_validate(action_snapshot.to_dict())
            if action.execution_status != ActionStatus.LEASED or action.lease_owner != worker_id:
                return False
            if attempt_snapshot.exists:
                return ActionAttempt.model_validate(attempt_snapshot.to_dict()) == attempt
            action.execution_status = (
                ActionStatus.FAILED_RETRYABLE
                if retry_after is not None
                else ActionStatus.FAILED_TERMINAL
            )
            action.lease_owner = None
            action.lease_started_at = None
            action.lease_expires_at = None
            action.retry_after = retry_after
            transaction.set(action_ref, action.model_dump(mode="json"))
            transaction.create(attempt_ref, attempt.model_dump(mode="json"))
            return True

        return await fail(transaction)

    async def record_action_attempt(self, attempt: ActionAttempt) -> bool:
        snapshots = [
            snapshot
            async for snapshot in self._client.collection_group("actions")
            .where("action_id", "==", attempt.action_id)
            .limit(1)
            .stream()
        ]
        if not snapshots:
            return False
        attempt_ref = snapshots[0].reference.collection("attempts").document(attempt.attempt_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def record(transaction: Any) -> bool:
            snapshot = await attempt_ref.get(transaction=transaction)
            if snapshot.exists:
                return ActionAttempt.model_validate(snapshot.to_dict()) == attempt
            transaction.create(attempt_ref, attempt.model_dump(mode="json"))
            return True

        return await record(transaction)

    async def list_action_attempts(self, action_id: str) -> list[ActionAttempt]:
        snapshots = [
            snapshot
            async for snapshot in self._client.collection_group("actions")
            .where("action_id", "==", action_id)
            .limit(1)
            .stream()
        ]
        if not snapshots:
            return []
        attempts = [
            snapshot async for snapshot in snapshots[0].reference.collection("attempts").stream()
        ]
        return [ActionAttempt.model_validate(snapshot.to_dict()) for snapshot in attempts]

    async def mark_action_verified(self, *, action_id: str, verified: bool) -> bool:
        snapshots = [
            snapshot
            async for snapshot in self._client.collection_group("actions")
            .where("action_id", "==", action_id)
            .limit(1)
            .stream()
        ]
        if not snapshots:
            return False
        action_ref = snapshots[0].reference
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def mark(transaction: Any) -> bool:
            snapshot = await action_ref.get(transaction=transaction)  # type: ignore[misc]
            if not snapshot.exists:
                return False
            action = PlannedAction.model_validate(snapshot.to_dict())
            if action.execution_status != ActionStatus.SUCCEEDED:
                return False
            action.execution_status = (
                ActionStatus.VERIFIED if verified else ActionStatus.VERIFICATION_FAILED
            )
            transaction.set(action_ref, action.model_dump(mode="json"))
            return True

        return await mark(transaction)

    async def store_approval(self, approval: ApprovalRequest) -> bool:
        approval_ref = (
            self._client.collection("incidents")
            .document(approval.incident_id)
            .collection("approvals")
            .document(approval.approval_id)
        )
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def store(transaction: Any) -> bool:
            existing = await approval_ref.get(transaction=transaction)
            if existing.exists:
                return (existing.to_dict() or {}).get(
                    "callback_token_hash"
                ) == approval.callback_token_hash
            transaction.create(approval_ref, approval.model_dump(mode="json"))
            return True

        return await store(transaction)

    async def get_approval(self, approval_id: str) -> ApprovalRequest | None:
        snapshots = [
            snapshot
            async for snapshot in self._client.collection_group("approvals")
            .where("approval_id", "==", approval_id)
            .limit(1)
            .stream()
        ]
        if not snapshots:
            return None
        return ApprovalRequest.model_validate(snapshots[0].to_dict())

    async def get_approval_by_callback_token_hash(
        self, callback_token_hash: str
    ) -> ApprovalRequest | None:
        snapshots = [
            snapshot
            async for snapshot in self._client.collection_group("approvals")
            .where("callback_token_hash", "==", callback_token_hash)
            .limit(1)
            .stream()
        ]
        if not snapshots:
            return None
        return ApprovalRequest.model_validate(snapshots[0].to_dict())

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
        approval_docs = [
            snapshot
            async for snapshot in self._client.collection_group("approvals")
            .where("approval_id", "==", approval_id)
            .limit(1)
            .stream()
        ]
        if not approval_docs:
            return False
        approval_ref = approval_docs[0].reference
        incident_ref = self._client.collection("incidents").document(
            str(approval_docs[0].reference.parent.parent.id)
        )
        update_ref = self._client.collection("telegramUpdates").document(update_id)
        outbox_ref = self._client.collection("outbox").document(outbox.outbox_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def consume(transaction: Any) -> bool:
            approval_snapshot = await approval_ref.get(transaction=transaction)  # type: ignore[misc]
            if not approval_snapshot.exists:
                return False
            approval = ApprovalRequest.model_validate(approval_snapshot.to_dict())
            incident_snapshot = await incident_ref.get(transaction=transaction)
            if not incident_snapshot.exists:
                return False
            incident = Incident.model_validate(incident_snapshot.to_dict())
            plan_ref = incident_ref.collection("plans").document(str(approval.plan_version))
            plan_snapshot = await plan_ref.get(transaction=transaction)
            if not plan_snapshot.exists:
                return False
            plan = RecoveryPlan.model_validate(plan_snapshot.to_dict())
            trip_ref = self._client.collection("trips").document(incident.trip_id)
            trip_snapshot = await trip_ref.get(transaction=transaction)
            if not trip_snapshot.exists:
                return False
            trip = Trip.model_validate(
                {**(trip_snapshot.to_dict() or {}), "items": [], "dependencies": []}
            )
            if (
                incident.status != IncidentStatus.WAITING_APPROVAL
                or trip.active_incident_id != approval.incident_id
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
                or approval.incident_id != outbox.command.incident_id
                or approval.plan_version != outbox.command.plan_version
            ):
                return False
            if (await update_ref.get(transaction=transaction)).exists or (
                await outbox_ref.get(transaction=transaction)
            ).exists:
                return False
            approval.status = ApprovalStatus.APPROVED
            approval.decided_at = now
            approval.consumed_update_id = update_id
            transaction.set(approval_ref, approval.model_dump(mode="json"))
            transaction.create(
                update_ref, {"payload_hash": canonical_hash({"approval_id": approval_id})}
            )
            transaction.create(outbox_ref, outbox.model_dump(mode="json"))
            return True

        return await consume(transaction)

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
        approval_docs = [
            snapshot
            async for snapshot in self._client.collection_group("approvals")
            .where("approval_id", "==", approval_id)
            .limit(1)
            .stream()
        ]
        if not approval_docs:
            return False
        approval_ref = approval_docs[0].reference
        incident_ref = self._client.collection("incidents").document(
            str(approval_ref.parent.parent.id)
        )
        update_ref = self._client.collection("telegramUpdates").document(update_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def decline(transaction: Any) -> bool:
            approval_snapshot = await approval_ref.get(transaction=transaction)  # type: ignore[misc]
            incident_snapshot = await incident_ref.get(transaction=transaction)
            update_snapshot = await update_ref.get(transaction=transaction)
            if (
                not approval_snapshot.exists
                or not incident_snapshot.exists
                or update_snapshot.exists
            ):
                return False
            approval = ApprovalRequest.model_validate(approval_snapshot.to_dict())
            incident = Incident.model_validate(incident_snapshot.to_dict())
            trip_ref = self._client.collection("trips").document(incident.trip_id)
            trip_snapshot = await trip_ref.get(transaction=transaction)
            if not trip_snapshot.exists:
                return False
            active_incident_id = (trip_snapshot.to_dict() or {}).get("active_incident_id")
            if (
                active_incident_id != approval.incident_id
                or incident.status != IncidentStatus.WAITING_APPROVAL
                or approval.status != ApprovalStatus.PENDING
                or approval.callback_token_hash != callback_token_hash
                or approval.telegram_user_id != telegram_user_id
                or approval.telegram_chat_id != telegram_chat_id
                or approval.expires_at <= now
            ):
                return False
            approval.status = ApprovalStatus.DECLINED
            approval.decided_at = now
            approval.consumed_update_id = update_id
            incident.status = IncidentStatus.CANCELLED
            incident.version += 1
            incident.updated_at = now
            transaction.set(approval_ref, approval.model_dump(mode="json"))
            transaction.set(incident_ref, incident.model_dump(mode="json"))
            transaction.create(
                update_ref,
                {
                    "payload_hash": canonical_hash(
                        {"approval_id": approval_id, "status": "declined"}
                    )
                },
            )
            return True

        return await decline(transaction)

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
        approval_docs = [
            snapshot
            async for snapshot in self._client.collection_group("approvals")
            .where("approval_id", "==", approval_id)
            .limit(1)
            .stream()
        ]
        if not approval_docs:
            return False
        approval_ref = approval_docs[0].reference
        incident_ref = self._client.collection("incidents").document(
            str(approval_ref.parent.parent.id)
        )
        update_ref = self._client.collection("telegramUpdates").document(update_id)
        outbox_ref = self._client.collection("outbox").document(outbox.outbox_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def request_replan(transaction: Any) -> bool:
            approval_snapshot = await approval_ref.get(transaction=transaction)  # type: ignore[misc]
            incident_snapshot = await incident_ref.get(transaction=transaction)
            update_snapshot = await update_ref.get(transaction=transaction)
            outbox_snapshot = await outbox_ref.get(transaction=transaction)
            if (
                not approval_snapshot.exists
                or not incident_snapshot.exists
                or update_snapshot.exists
                or outbox_snapshot.exists
            ):
                return False
            approval = ApprovalRequest.model_validate(approval_snapshot.to_dict())
            incident = Incident.model_validate(incident_snapshot.to_dict())
            trip_ref = self._client.collection("trips").document(incident.trip_id)
            trip_snapshot = await trip_ref.get(transaction=transaction)
            if not trip_snapshot.exists:
                return False
            expected_incident = (
                IncidentStatus.CANCELLED if resume_cancelled else IncidentStatus.WAITING_APPROVAL
            )
            expected_approval = (
                ApprovalStatus.DECLINED if resume_cancelled else ApprovalStatus.PENDING
            )
            if (
                (trip_snapshot.to_dict() or {}).get("active_incident_id") != approval.incident_id
                or incident.status != expected_incident
                or approval.status != expected_approval
                or approval.callback_token_hash != callback_token_hash
                or approval.telegram_user_id != telegram_user_id
                or approval.telegram_chat_id != telegram_chat_id
                or outbox.command.incident_id != approval.incident_id
                or outbox.command.type != WorkflowCommandType.REPLAN
            ):
                return False
            approval.status = ApprovalStatus.SUPERSEDED
            approval.decided_at = now
            approval.consumed_update_id = update_id
            incident.status = IncidentStatus.PLANNING
            incident.version += 1
            incident.updated_at = now
            transaction.set(approval_ref, approval.model_dump(mode="json"))
            transaction.set(incident_ref, incident.model_dump(mode="json"))
            transaction.create(
                update_ref,
                {"payload_hash": canonical_hash({"approval_id": approval_id, "status": "replan"})},
            )
            transaction.create(outbox_ref, outbox.model_dump(mode="json"))
            return True

        return await request_replan(transaction)

    async def expire_approval_and_enqueue_replan(
        self,
        *,
        approval_id: str,
        now: datetime,
        outbox: OutboxRecord,
    ) -> bool:
        approval_docs = [
            snapshot
            async for snapshot in self._client.collection_group("approvals")
            .where("approval_id", "==", approval_id)
            .limit(1)
            .stream()
        ]
        if not approval_docs:
            return False
        approval_ref = approval_docs[0].reference
        incident_ref = self._client.collection("incidents").document(
            str(approval_ref.parent.parent.id)
        )
        outbox_ref = self._client.collection("outbox").document(outbox.outbox_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def expire(transaction: Any) -> bool:
            approval_snapshot = await approval_ref.get(transaction=transaction)  # type: ignore[misc]
            incident_snapshot = await incident_ref.get(transaction=transaction)
            outbox_snapshot = await outbox_ref.get(transaction=transaction)
            if not approval_snapshot.exists or not incident_snapshot.exists:
                return False
            approval = ApprovalRequest.model_validate(approval_snapshot.to_dict())
            if approval.status == ApprovalStatus.EXPIRED:
                return outbox_snapshot.exists and (
                    (outbox_snapshot.to_dict() or {}).get("command", {}).get("command_id")
                    == outbox.command.command_id
                )
            incident = Incident.model_validate(incident_snapshot.to_dict())
            if (
                incident.status != IncidentStatus.WAITING_APPROVAL
                or approval.status != ApprovalStatus.PENDING
                or approval.expires_at > now
                or outbox_snapshot.exists
                or outbox.command.incident_id != approval.incident_id
            ):
                return False
            approval.status = ApprovalStatus.EXPIRED
            approval.decided_at = now
            incident.status = IncidentStatus.PLANNING
            incident.version += 1
            incident.updated_at = now
            transaction.set(approval_ref, approval.model_dump(mode="json"))
            transaction.set(incident_ref, incident.model_dump(mode="json"))
            transaction.create(outbox_ref, outbox.model_dump(mode="json"))
            return True

        return await expire(transaction)

    async def claim_telegram_update(self, *, update_id: str, payload_hash: str) -> bool:
        update_ref = self._client.collection("telegramUpdates").document(update_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def claim(transaction: Any) -> bool:
            existing = await update_ref.get(transaction=transaction)
            if not existing.exists:
                transaction.create(update_ref, {"payload_hash": payload_hash})
                return True
            stored_hash = (existing.to_dict() or {}).get("payload_hash")
            if stored_hash != payload_hash:
                raise EventPayloadConflict(
                    f"Telegram update {update_id!r} was reused with new payload"
                )
            return False

        return await claim(transaction)

    async def claim_telegram_rate_slot(
        self,
        *,
        telegram_user_id: str,
        update_kind: str,
        window_started_at: datetime,
        limit: int,
    ) -> bool:
        rate_key = canonical_hash(
            {
                "telegram_user_id": telegram_user_id,
                "update_kind": update_kind,
                "window_started_at": window_started_at,
            }
        )
        rate_ref = self._client.collection("telegramRateLimits").document(rate_key)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def claim(transaction: Any) -> bool:
            snapshot = await rate_ref.get(transaction=transaction)
            count = int((snapshot.to_dict() or {}).get("count", 0)) if snapshot.exists else 0
            if count >= limit:
                return False
            transaction.set(
                rate_ref,
                {
                    "count": count + 1,
                    "update_kind": update_kind,
                    "window_started_at": window_started_at,
                },
            )
            return True

        return await claim(transaction)

    async def enqueue_outbox_once(self, outbox: OutboxRecord) -> bool:
        outbox_ref = self._client.collection("outbox").document(outbox.outbox_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def enqueue(transaction: Any) -> bool:
            existing = await outbox_ref.get(transaction=transaction)
            if not existing.exists:
                transaction.create(outbox_ref, outbox.model_dump(mode="json"))
                return True
            return bool(
                (existing.to_dict() or {}).get("command", {}).get("command_id")
                == outbox.command.command_id
            )

        return await enqueue(transaction)

    async def get_outbox(self, outbox_id: str) -> OutboxRecord | None:
        snapshot = await self._client.collection("outbox").document(outbox_id).get()
        if not snapshot.exists:
            return None
        return OutboxRecord.model_validate(snapshot.to_dict())

    async def list_pending_outbox(self, *, limit: int = 100) -> list[OutboxRecord]:
        # Equality filtering is covered by Firestore's built-in single-field
        # index.  Sort and bound locally to avoid requiring a deployment-specific
        # compound ``status + created_at`` index for the durable retry loop.
        records = [
            OutboxRecord.model_validate(snapshot.to_dict())
            async for snapshot in self._client.collection("outbox")
            .where("status", "==", OutboxStatus.PENDING.value)
            .stream()
        ]
        records.sort(key=lambda record: record.created_at)
        return records[:limit]

    async def mark_outbox_published(self, *, outbox_id: str, published_at: datetime) -> bool:
        outbox_ref = self._client.collection("outbox").document(outbox_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def mark(transaction: Any) -> bool:
            snapshot = await outbox_ref.get(transaction=transaction)
            if not snapshot.exists:
                return False
            record = OutboxRecord.model_validate(snapshot.to_dict())
            if record.status == OutboxStatus.PUBLISHED:
                return True
            record.status = OutboxStatus.PUBLISHED
            record.published_at = published_at
            transaction.set(outbox_ref, record.model_dump(mode="json"))
            return True

        return await mark(transaction)

    async def claim_workflow_command(
        self,
        *,
        command: WorkflowCommand,
        worker_id: str,
        lease_expires_at: datetime,
        now: datetime,
    ) -> bool:
        command_ref = self._client.collection("workflowCommands").document(command.command_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def claim(transaction: Any) -> bool:
            if command.not_before is not None and command.not_before > now:
                return False
            snapshot = await command_ref.get(transaction=transaction)
            payload_hash = canonical_hash(command)
            if snapshot.exists:
                state = WorkflowCommandState.model_validate(snapshot.to_dict())
                if state.payload_hash != payload_hash:
                    raise EventPayloadConflict(
                        f"Workflow command {command.command_id!r} was reused with new payload"
                    )
                if state.status == WorkflowCommandStatus.COMPLETED:
                    return False
                if state.lease_expires_at is not None and state.lease_expires_at > now:
                    return False
            state = WorkflowCommandState(
                command=command,
                payload_hash=payload_hash,
                status=WorkflowCommandStatus.LEASED,
                lease_owner=worker_id,
                lease_expires_at=lease_expires_at,
            )
            transaction.set(command_ref, state.model_dump(mode="json"))
            return True

        return await claim(transaction)

    async def complete_workflow_command(
        self, *, command_id: str, worker_id: str, completed_at: datetime
    ) -> bool:
        command_ref = self._client.collection("workflowCommands").document(command_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def complete(transaction: Any) -> bool:
            snapshot = await command_ref.get(transaction=transaction)
            if not snapshot.exists:
                return False
            state = WorkflowCommandState.model_validate(snapshot.to_dict())
            if state.status == WorkflowCommandStatus.COMPLETED:
                return True
            if state.lease_owner != worker_id:
                return False
            state.status = WorkflowCommandStatus.COMPLETED
            state.lease_owner = None
            state.lease_expires_at = None
            state.completed_at = completed_at
            transaction.set(command_ref, state.model_dump(mode="json"))
            return True

        return await complete(transaction)

    async def get_workflow_command_state(self, command_id: str) -> WorkflowCommandState | None:
        snapshot = await self._client.collection("workflowCommands").document(command_id).get()
        if not snapshot.exists:
            return None
        return WorkflowCommandState.model_validate(snapshot.to_dict())

    async def apply_demo_provider_effect(
        self,
        *,
        resource_id: str,
        effect_key: str,
        desired_state: dict[str, object],
    ) -> bool:
        resource_ref = self._client.collection("demoProviderState").document(resource_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def apply(transaction: Any) -> bool:
            existing = await resource_ref.get(transaction=transaction)
            if existing.exists and (existing.to_dict() or {}).get("effect_key") == effect_key:
                return True
            transaction.set(
                resource_ref,
                {"effect_key": effect_key, "desired_state": desired_state},
            )
            return True

        return await apply(transaction)

    async def get_demo_provider_state(self, resource_id: str) -> dict[str, object] | None:
        snapshot = await self._client.collection("demoProviderState").document(resource_id).get()
        if not snapshot.exists:
            return None
        payload = snapshot.to_dict() or {}
        desired_state = payload.get("desired_state")
        if not isinstance(desired_state, dict):
            return None
        return {"effect_key": str(payload.get("effect_key", "")), "desired_state": desired_state}

    async def get_traveler(self, telegram_user_id: str) -> TravelerProfile | None:
        snapshot = await self._client.collection("telegramUsers").document(telegram_user_id).get()
        if not snapshot.exists:
            return None
        return TravelerProfile.model_validate(snapshot.to_dict())

    async def get_traveler_by_user_id(self, user_id: str) -> TravelerProfile | None:
        query = self._client.collection("telegramUsers").where("user_id", "==", user_id).limit(1)
        async for snapshot in query.stream():
            if snapshot.exists:
                return TravelerProfile.model_validate(snapshot.to_dict())
        return None

    async def save_traveler(self, traveler: TravelerProfile) -> None:
        await (
            self._client.collection("telegramUsers")
            .document(traveler.telegram_user_id)
            .set(traveler.model_dump(mode="json"))
        )

    async def activate_traveler_policy(
        self, *, traveler: TravelerProfile, policy: AutonomyPolicy
    ) -> bool:
        traveler_ref = self._client.collection("telegramUsers").document(traveler.telegram_user_id)
        policy_ref = (
            self._client.collection("travelers")
            .document(policy.user_id)
            .collection("policies")
            .document(str(policy.version))
        )
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def activate(transaction: Any) -> bool:
            traveler_snapshot = await traveler_ref.get(transaction=transaction)
            policy_snapshot = await policy_ref.get(transaction=transaction)
            if not traveler_snapshot.exists:
                return False
            current = TravelerProfile.model_validate(traveler_snapshot.to_dict())
            if current.user_id != traveler.user_id:
                return False
            if policy_snapshot.exists:
                existing = AutonomyPolicy.model_validate(policy_snapshot.to_dict())
                return existing == policy and current.active_policy_version == policy.version
            expected_previous = policy.version - 1 or None
            if current.active_policy_version != expected_previous:
                return False
            transaction.create(policy_ref, policy.model_dump(mode="json"))
            transaction.set(traveler_ref, traveler.model_dump(mode="json"))
            return True

        return await activate(transaction)

    async def get_traveler_policy(self, *, user_id: str, version: int) -> AutonomyPolicy | None:
        snapshot = await (
            self._client.collection("travelers")
            .document(user_id)
            .collection("policies")
            .document(str(version))
            .get()
        )
        if not snapshot.exists:
            return None
        return AutonomyPolicy.model_validate(snapshot.to_dict())

    async def get_notification(self, notification_id: str) -> OutboundNotification | None:
        snapshot = await self._client.collection("notifications").document(notification_id).get()
        if not snapshot.exists:
            return None
        return OutboundNotification.model_validate(snapshot.to_dict())

    async def store_notification_intent(self, notification: OutboundNotification) -> bool:
        notification_ref = self._client.collection("notifications").document(
            notification.notification_id
        )
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def store(transaction: Any) -> bool:
            existing = await notification_ref.get(transaction=transaction)
            if existing.exists:
                payload = existing.to_dict() or {}
                return (
                    payload.get("view_hash") == notification.view_hash
                    and payload.get("chat_id") == notification.chat_id
                )
            transaction.create(notification_ref, notification.model_dump(mode="json"))
            return True

        return await store(transaction)

    async def mark_notification_sent(
        self, *, notification_id: str, message_id: int, sent_at: datetime
    ) -> bool:
        notification_ref = self._client.collection("notifications").document(notification_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def mark(transaction: Any) -> bool:
            snapshot = await notification_ref.get(transaction=transaction)
            if not snapshot.exists:
                return False
            notification = OutboundNotification.model_validate(snapshot.to_dict())
            if notification.status == "SENT":
                return notification.message_id == message_id
            notification.status = "SENT"
            notification.message_id = message_id
            notification.sent_at = sent_at
            transaction.set(notification_ref, notification.model_dump(mode="json"))
            return True

        return await mark(transaction)

    async def mark_notification_unknown(
        self, *, notification_id: str, unknown_at: datetime
    ) -> bool:
        notification_ref = self._client.collection("notifications").document(notification_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def mark(transaction: Any) -> bool:
            snapshot = await notification_ref.get(transaction=transaction)
            if not snapshot.exists:
                return False
            notification = OutboundNotification.model_validate(snapshot.to_dict())
            if notification.status == "SENT":
                return False
            notification.status = "UNKNOWN"
            notification.unknown_at = unknown_at
            transaction.set(notification_ref, notification.model_dump(mode="json"))
            return True

        return await mark(transaction)

    async def mark_notification_blocked(
        self, *, notification_id: str, blocked_at: datetime, failure_code: str
    ) -> bool:
        notification_ref = self._client.collection("notifications").document(notification_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def mark(transaction: Any) -> bool:
            snapshot = await notification_ref.get(transaction=transaction)
            if not snapshot.exists:
                return False
            notification = OutboundNotification.model_validate(snapshot.to_dict())
            if notification.status == "SENT":
                return False
            notification.status = "BLOCKED"
            notification.blocked_at = blocked_at
            notification.failure_code = failure_code
            transaction.set(notification_ref, notification.model_dump(mode="json"))
            return True

        return await mark(transaction)

    async def store_ai_handoff(self, handoff: AiConnectionHandoff) -> bool:
        handoff_ref = self._client.collection("aiConnectionStates").document(handoff.state_hash)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def store(transaction: Any) -> bool:
            if (await handoff_ref.get(transaction=transaction)).exists:
                return False
            transaction.create(handoff_ref, handoff.model_dump(mode="json"))
            return True

        return await store(transaction)

    async def consume_ai_handoff(
        self,
        *,
        state_hash: str,
        telegram_user_id: str,
        telegram_chat_id: str,
        now: datetime,
    ) -> AiConnectionHandoff | None:
        handoff_ref = self._client.collection("aiConnectionStates").document(state_hash)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def consume(transaction: Any) -> AiConnectionHandoff | None:
            snapshot = await handoff_ref.get(transaction=transaction)
            if not snapshot.exists:
                return None
            handoff = AiConnectionHandoff.model_validate(snapshot.to_dict())
            if (
                handoff.telegram_user_id != telegram_user_id
                or handoff.telegram_chat_id != telegram_chat_id
                or handoff.consumed_at is not None
                or handoff.expires_at <= now
            ):
                return None
            handoff.consumed_at = now
            transaction.set(handoff_ref, handoff.model_dump(mode="json"))
            return handoff

        return await consume(transaction)

    async def get_ai_connection(self, telegram_user_id: str) -> AiConnection | None:
        snapshot = await (
            self._client.collection("telegramUsers")
            .document(telegram_user_id)
            .collection("connections")
            .document("gemini")
            .get()
        )
        if not snapshot.exists:
            return None
        return AiConnection.model_validate(snapshot.to_dict())

    async def save_ai_connection(self, connection: AiConnection) -> None:
        await (
            self._client.collection("telegramUsers")
            .document(connection.telegram_user_id)
            .collection("connections")
            .document("gemini")
            .set(connection.model_dump(mode="json"))
        )

    async def store_calendar_oauth_state(self, state: CalendarOAuthState) -> bool:
        state_ref = self._client.collection("calendarOAuthStates").document(state.state_hash)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def store(transaction: Any) -> bool:
            if (await state_ref.get(transaction=transaction)).exists:
                return False
            transaction.create(state_ref, state.model_dump(mode="json"))
            return True

        return await store(transaction)

    async def get_calendar_oauth_state(self, state_hash: str) -> CalendarOAuthState | None:
        snapshot = await self._client.collection("calendarOAuthStates").document(state_hash).get()
        if not snapshot.exists:
            return None
        return CalendarOAuthState.model_validate(snapshot.to_dict())

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
        state_ref = self._client.collection("calendarOAuthStates").document(state_hash)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def consume(transaction: Any) -> CalendarOAuthState | None:
            snapshot = await state_ref.get(transaction=transaction)
            if not snapshot.exists:
                return None
            state = CalendarOAuthState.model_validate(snapshot.to_dict())
            if (
                state.telegram_user_id != telegram_user_id
                or state.telegram_chat_id != telegram_chat_id
                or state.redirect_uri != redirect_uri
                or state.code_verifier_hash != code_verifier_hash
                or state.consumed_at is not None
                or state.expires_at <= now
            ):
                return None
            state.consumed_at = now
            transaction.set(state_ref, state.model_dump(mode="json"))
            return state

        return await consume(transaction)

    async def get_calendar_connection(self, telegram_user_id: str) -> CalendarConnection | None:
        snapshot = await (
            self._client.collection("telegramUsers")
            .document(telegram_user_id)
            .collection("connections")
            .document("calendar")
            .get()
        )
        if not snapshot.exists:
            return None
        return CalendarConnection.model_validate(snapshot.to_dict())

    async def save_calendar_connection(self, connection: CalendarConnection) -> None:
        await (
            self._client.collection("telegramUsers")
            .document(connection.telegram_user_id)
            .collection("connections")
            .document("calendar")
            .set(connection.model_dump(mode="json"))
        )

    async def store_gmail_oauth_state(self, state: GmailOAuthState) -> bool:
        state_ref = self._client.collection("gmailOAuthStates").document(state.state_hash)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def store(transaction: Any) -> bool:
            if (await state_ref.get(transaction=transaction)).exists:
                return False
            transaction.create(state_ref, state.model_dump(mode="json"))
            return True

        return await store(transaction)

    async def get_gmail_oauth_state(self, state_hash: str) -> GmailOAuthState | None:
        snapshot = await self._client.collection("gmailOAuthStates").document(state_hash).get()
        if not snapshot.exists:
            return None
        return GmailOAuthState.model_validate(snapshot.to_dict())

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
        state_ref = self._client.collection("gmailOAuthStates").document(state_hash)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def consume(transaction: Any) -> GmailOAuthState | None:
            snapshot = await state_ref.get(transaction=transaction)
            if not snapshot.exists:
                return None
            state = GmailOAuthState.model_validate(snapshot.to_dict())
            if (
                state.telegram_user_id != telegram_user_id
                or state.telegram_chat_id != telegram_chat_id
                or state.redirect_uri != redirect_uri
                or state.code_verifier_hash != code_verifier_hash
                or state.consumed_at is not None
                or state.expires_at <= now
            ):
                return None
            state.consumed_at = now
            transaction.set(state_ref, state.model_dump(mode="json"))
            return state

        return await consume(transaction)

    async def get_gmail_connection(self, telegram_user_id: str) -> GmailConnection | None:
        snapshot = await (
            self._client.collection("telegramUsers")
            .document(telegram_user_id)
            .collection("connections")
            .document("gmail")
            .get()
        )
        if not snapshot.exists:
            return None
        return GmailConnection.model_validate(snapshot.to_dict())

    async def save_gmail_connection(self, connection: GmailConnection) -> None:
        await (
            self._client.collection("telegramUsers")
            .document(connection.telegram_user_id)
            .collection("connections")
            .document("gmail")
            .set(connection.model_dump(mode="json"))
        )

    async def save_expense_once(self, expense: TripExpense) -> bool:
        expense_ref = self._client.collection("tripExpenses").document(expense.expense_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def store(transaction: Any) -> bool:
            snapshot = await expense_ref.get(transaction=transaction)
            if snapshot.exists:
                return TripExpense.model_validate(snapshot.to_dict()) == expense
            transaction.create(expense_ref, expense.model_dump(mode="json"))
            return True

        return await store(transaction)

    async def get_expense(self, expense_id: str) -> TripExpense | None:
        snapshot = await self._client.collection("tripExpenses").document(expense_id).get()
        if not snapshot.exists:
            return None
        return TripExpense.model_validate(snapshot.to_dict())

    async def list_expenses(self, trip_id: str) -> list[TripExpense]:
        query = self._client.collection("tripExpenses").where("trip_id", "==", trip_id)
        return [TripExpense.model_validate(doc.to_dict()) async for doc in query.stream()]

    async def save_trip_document_once(self, document: TripDocument) -> bool:
        document_ref = self._client.collection("tripDocuments").document(document.document_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def store(transaction: Any) -> bool:
            snapshot = await document_ref.get(transaction=transaction)
            if snapshot.exists:
                return TripDocument.model_validate(snapshot.to_dict()) == document
            transaction.create(document_ref, document.model_dump(mode="json"))
            return True

        return await store(transaction)

    async def list_trip_documents(self, trip_id: str) -> list[TripDocument]:
        query = self._client.collection("tripDocuments").where("trip_id", "==", trip_id)
        return [TripDocument.model_validate(doc.to_dict()) async for doc in query.stream()]

    async def save_financial_item_once(self, item: OpenFinancialItem) -> bool:
        item_ref = self._client.collection("tripFinancialItems").document(item.financial_item_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def store(transaction: Any) -> bool:
            snapshot = await item_ref.get(transaction=transaction)
            if snapshot.exists:
                return OpenFinancialItem.model_validate(snapshot.to_dict()) == item
            transaction.create(item_ref, item.model_dump(mode="json"))
            return True

        return await store(transaction)

    async def list_financial_items(self, trip_id: str) -> list[OpenFinancialItem]:
        query = self._client.collection("tripFinancialItems").where("trip_id", "==", trip_id)
        return [OpenFinancialItem.model_validate(doc.to_dict()) async for doc in query.stream()]

    async def settle_financial_item(
        self,
        *,
        financial_item_id: str,
        owner_user_id: str,
        actual_amount: Money,
        settled_at: datetime,
    ) -> OpenFinancialItem | None:
        item_ref = self._client.collection("tripFinancialItems").document(financial_item_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def settle(transaction: Any) -> OpenFinancialItem | None:
            snapshot = await item_ref.get(transaction=transaction)
            if not snapshot.exists:
                return None
            item = OpenFinancialItem.model_validate(snapshot.to_dict())
            if item.owner_user_id != owner_user_id:
                return None
            if item.status == "SETTLED":
                return item if item.actual_amount == actual_amount else None
            if item.expected_amount != actual_amount:
                return None
            item.status = "SETTLED"
            item.actual_amount = actual_amount
            item.settled_at = settled_at
            item.updated_at = settled_at
            transaction.set(item_ref, item.model_dump(mode="json"))
            return item

        return await settle(transaction)

    async def set_trip_status(
        self,
        *,
        trip_id: str,
        owner_user_id: str,
        status: TripStatus,
        updated_at: datetime,
    ) -> bool:
        trip_ref = self._client.collection("trips").document(trip_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def update(transaction: Any) -> bool:
            snapshot = await trip_ref.get(transaction=transaction)
            if (
                not snapshot.exists
                or (snapshot.to_dict() or {}).get("owner_user_id") != owner_user_id
            ):
                return False
            transaction.update(
                trip_ref,
                {"status": status.value, "updated_at": updated_at},
            )
            return True

        return await update(transaction)
