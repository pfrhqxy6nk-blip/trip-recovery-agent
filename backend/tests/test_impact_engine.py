from datetime import UTC, datetime

from app.demo_data import build_demo_trip
from app.services.impact import DeterministicImpactEngine

from tests.helpers import disruption_event


def test_infeasible_connection_is_calculated_deterministically() -> None:
    impact = DeterministicImpactEngine().calculate(disruption_event(), build_demo_trip())

    assert impact.arrival_delta_minutes == 105
    assert impact.connection_feasible is False
    assert impact.affected_item_ids == [
        "flight-lh1792",
        "airport-transfer",
        "hotel-arrival",
    ]
    assert impact.affected_dependency_ids == [
        "dep-lo351-lh1792",
        "dep-lh1792-transfer",
        "dep-transfer-hotel",
    ]
    assert impact.buffer_violations[0].available_minutes == -50
    assert impact.buffer_violations[0].required_minutes == 45


def test_feasible_connection_remains_feasible() -> None:
    event = disruption_event(new_arrival=datetime(2026, 8, 20, 18, 5, tzinfo=UTC))

    impact = DeterministicImpactEngine().calculate(event, build_demo_trip())

    assert impact.arrival_delta_minutes == 5
    assert impact.connection_feasible is True
    assert impact.affected_item_ids == []
    assert impact.affected_dependency_ids == []
    assert impact.buffer_violations == []
