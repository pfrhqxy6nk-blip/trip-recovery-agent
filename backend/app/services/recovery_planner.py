from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.domain import Incident, Trip
from app.models.enums import ActionCategory, ItemType
from app.models.money import Money
from app.models.policy import AutonomyPolicy, PolicyCandidate
from app.models.recovery import PlannedAction, RecoveryOption, RecoveryPlan
from app.services.canonical_hash import canonical_hash, semantic_effect_key
from app.services.policy_engine import PolicyEngine

ActionSpec = tuple[str, ActionCategory, str, str, dict[str, object], Money, bool]


class RecoveryPlanningError(ValueError):
    pass


class CanonicalRecoveryPlanner:
    """Creates the single demonstrable recovery plan from deterministic impact facts."""

    _option_arrival = datetime(2026, 8, 20, 23, 15, tzinfo=UTC)

    def __init__(self, policy_engine: PolicyEngine | None = None) -> None:
        self._policy_engine = policy_engine or PolicyEngine()

    def create_plan(
        self,
        *,
        incident: Incident,
        policy: AutonomyPolicy,
        now: datetime,
        version: int = 1,
        trip: Trip | None = None,
        live_option: RecoveryOption | None = None,
    ) -> RecoveryPlan:
        impact = incident.deterministic_impact
        if impact is None or impact.connection_feasible:
            raise RecoveryPlanningError("canonical recovery requires an infeasible connection")
        if trip is not None and trip.trip_id != incident.trip_id:
            raise RecoveryPlanningError("trip does not match the recovery incident")
        if trip is None and (
            incident.trip_id != "demo-trip-001" or impact.arrival_delta_minutes != 105
        ):
            raise RecoveryPlanningError("a validated trip is required outside the demo scenario")

        option_arrival = self._option_arrival
        option_route = "MUC-LIS"
        provider_option_id = "demo-option-muc-lis-001"
        action_specs: tuple[ActionSpec, ...]
        if live_option is not None:
            if trip is None:
                raise RecoveryPlanningError("a trip is required for a live recovery quote")
            option = live_option
            option_arrival = live_option.arrival_at
            action_specs = self._trip_action_specs(trip, option_arrival, live_option=live_option)
        elif trip is None or trip.trip_id == "demo-trip-001":
            action_specs = self._canonical_action_specs(option_arrival)
        else:
            flights = sorted(
                (item for item in trip.items if item.type == ItemType.FLIGHT),
                key=lambda item: item.start_at,
            )
            if len(flights) < 2:
                raise RecoveryPlanningError("recovery requires at least two connected flights")
            final_flight = flights[-1]
            option_arrival = final_flight.end_at + timedelta(minutes=130)
            option_route = f"{final_flight.origin}-{final_flight.destination}"
            provider_option_id = f"demo-option-{trip.trip_id}"
            action_specs = self._trip_action_specs(trip, option_arrival)

        if live_option is None:
            option = RecoveryOption(
                provider="demo-flight",
                provider_option_id=provider_option_id,
                option_fingerprint=canonical_hash(
                    {"flight": option_route, "arrival_at": option_arrival, "cost": 3_400}
                ),
                incremental_cost=Money(currency="EUR", minor_units=3_400),
                quote_expires_at=now + timedelta(minutes=15),
                provider_snapshot_hash=canonical_hash(
                    {"provider": "demo-flight", "option": provider_option_id}
                ),
                arrival_at=option_arrival,
                reversible=True,
                reversible_until=now + timedelta(minutes=10),
            )
        impact_hash = canonical_hash(impact)
        actions: list[PlannedAction] = []
        cumulative_auto_spend = Money(currency="EUR", minor_units=0)
        transfer_action_id = next(
            (
                f"{incident.incident_id}:v{version}:{label}"
                for label, *_ in action_specs
                if label == "transfer"
            ),
            None,
        )
        for label, category, provider, target, desired_state, cost, reversible in action_specs:
            action_id = f"{incident.incident_id}:v{version}:{label}"
            decision = self._policy_engine.decide(
                policy,
                PolicyCandidate(
                    action_id=action_id,
                    category=category,
                    cost=cost,
                    reversible=reversible,
                ),
                cumulative_auto_spend,
            )
            if decision.verdict.value == "AUTO_APPROVED":
                cumulative_auto_spend = cumulative_auto_spend.add(cost)
            actions.append(
                PlannedAction(
                    action_id=action_id,
                    incident_id=incident.incident_id,
                    plan_version=version,
                    category=category,
                    provider=provider,
                    target_external_id=target,
                    desired_state=desired_state,
                    prerequisites=(
                        [transfer_action_id]
                        if label == "flight" and transfer_action_id is not None
                        else []
                    ),
                    cost=cost,
                    reversible=reversible,
                    verification_spec={"resource_id": f"{provider}:{target}"},
                    policy_decision=decision,
                    effect_key=semantic_effect_key(
                        incident.incident_id, provider, target, "apply", desired_state
                    ),
                )
            )
        draft = {
            "plan_id": f"{incident.incident_id}:v{version}",
            "incident_id": incident.incident_id,
            "version": version,
            "source_incident_version": incident.version,
            "policy_version": policy.version,
            "impact_hash": impact_hash,
            "selected_option": option,
            "actions": actions,
            "total_incremental_cost": option.incremental_cost,
            "valid_until": option.quote_expires_at,
        }
        return RecoveryPlan.model_validate({**draft, "plan_hash": canonical_hash(draft)})

    @staticmethod
    def _canonical_action_specs(
        option_arrival: datetime,
    ) -> tuple[ActionSpec, ...]:
        del option_arrival
        return (
            (
                "transfer",
                ActionCategory.REVERSIBLE_CHANGE,
                "demo-transfer",
                "transfer-001",
                {"pickup_at": "2026-08-20T23:35:00Z"},
                Money(currency="EUR", minor_units=0),
                True,
            ),
            (
                "hotel",
                ActionCategory.REVERSIBLE_CHANGE,
                "demo-hotel",
                "hotel-001",
                {"expected_arrival_at": "2026-08-20T23:15:00Z"},
                Money(currency="EUR", minor_units=0),
                True,
            ),
            (
                "calendar",
                ActionCategory.CALENDAR,
                "demo-calendar",
                "calendar-001",
                {"arrival_at": "2026-08-20T23:15:00Z"},
                Money(currency="EUR", minor_units=0),
                True,
            ),
            (
                "flight",
                ActionCategory.FLIGHT_RECOVERY,
                "demo-flight",
                "booking-001",
                {
                    "option_id": "demo-option-muc-lis-001",
                    "arrival_at": "2026-08-20T23:15:00Z",
                },
                Money(currency="EUR", minor_units=3_400),
                True,
            ),
        )

    @staticmethod
    def _trip_action_specs(
        trip: Trip, option_arrival: datetime, *, live_option: RecoveryOption | None = None
    ) -> tuple[ActionSpec, ...]:
        timestamp = option_arrival.isoformat().replace("+00:00", "Z")
        specs: list[ActionSpec] = []
        transfer = next((item for item in trip.items if item.type == ItemType.TRANSFER), None)
        if transfer is not None:
            specs.append(
                (
                    "transfer",
                    ActionCategory.REVERSIBLE_CHANGE,
                    "demo-transfer",
                    f"{trip.trip_id}:{transfer.external_id or transfer.item_id}",
                    {"pickup_at": (option_arrival + timedelta(minutes=20)).isoformat()},
                    Money(currency="EUR", minor_units=0),
                    True,
                )
            )
        hotel = next((item for item in trip.items if item.type == ItemType.HOTEL_ARRIVAL), None)
        if hotel is not None:
            specs.append(
                (
                    "hotel",
                    ActionCategory.SERVICE_MESSAGE,
                    "demo-hotel",
                    f"{trip.trip_id}:{hotel.external_id or hotel.item_id}",
                    {
                        "expected_arrival_at": timestamp,
                        "hotel_name": hotel.location or hotel.provider,
                        "booking_reference": hotel.external_id,
                        "contact_email": hotel.contact_email,
                    },
                    Money(currency="EUR", minor_units=0),
                    True,
                )
            )
        specs.append(
            (
                "calendar",
                ActionCategory.CALENDAR,
                "demo-calendar",
                f"calendar:{trip.trip_id}",
                {"arrival_at": timestamp},
                Money(currency="EUR", minor_units=0),
                True,
            )
        )
        final_flight = max(
            (item for item in trip.items if item.type == ItemType.FLIGHT),
            key=lambda item: item.end_at,
        )
        specs.append(
            (
                "flight",
                ActionCategory.FLIGHT_RECOVERY,
                live_option.provider if live_option is not None else "demo-flight",
                f"{trip.trip_id}:{final_flight.external_id or final_flight.item_id}",
                {
                    "option_id": (
                        live_option.provider_option_id
                        if live_option is not None
                        else f"demo-option-{trip.trip_id}"
                    ),
                    "arrival_at": timestamp,
                },
                live_option.incremental_cost
                if live_option is not None
                else Money(currency="EUR", minor_units=3_400),
                live_option.reversible if live_option is not None else True,
            )
        )
        return tuple(specs)
