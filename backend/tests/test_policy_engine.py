from datetime import UTC, datetime

from app.models.enums import ActionCategory, PolicyMode, PolicyReasonCode, PolicyVerdict
from app.models.money import Money
from app.models.policy import AutonomyPolicy, PolicyCandidate
from app.services.policy_engine import PolicyEngine


def policy(**overrides: object) -> AutonomyPolicy:
    values: dict[str, object] = {
        "policy_id": "policy-1",
        "user_id": "traveler-1",
        "version": 1,
        "automatic_spending_enabled": True,
        "incident_spending_limit": Money(currency="EUR", minor_units=2_000),
        "created_at": datetime(2026, 8, 16, tzinfo=UTC),
        "updated_at": datetime(2026, 8, 16, tzinfo=UTC),
    }
    values.update(overrides)
    return AutonomyPolicy.model_validate(values)


def candidate(**overrides: object) -> PolicyCandidate:
    values: dict[str, object] = {
        "action_id": "action-1",
        "category": ActionCategory.REVERSIBLE_CHANGE,
        "cost": Money(currency="EUR", minor_units=1_500),
        "reversible": True,
    }
    values.update(overrides)
    return PolicyCandidate.model_validate(values)


def test_reversible_eur15_is_auto_allowed_under_eur20_limit() -> None:
    decision = PolicyEngine().decide(policy(), candidate())

    assert decision.verdict == PolicyVerdict.AUTO_APPROVED
    assert decision.reason_codes == (PolicyReasonCode.AUTO_ALLOWED,)
    assert decision.remaining_automatic_spend == Money(currency="EUR", minor_units=2_000)


def test_eur34_requires_approval_under_eur20_limit() -> None:
    decision = PolicyEngine().decide(
        policy(), candidate(cost=Money(currency="EUR", minor_units=3_400))
    )

    assert decision.verdict == PolicyVerdict.APPROVAL_REQUIRED
    assert PolicyReasonCode.SPENDING_LIMIT_EXCEEDED in decision.reason_codes


def test_cumulative_spending_requires_approval() -> None:
    decision = PolicyEngine().decide(
        policy(),
        candidate(cost=Money(currency="EUR", minor_units=1_000)),
        Money(currency="EUR", minor_units=1_500),
    )

    assert decision.verdict == PolicyVerdict.APPROVAL_REQUIRED
    assert decision.remaining_automatic_spend == Money(currency="EUR", minor_units=500)


def test_mandatory_risk_overrides_allowance() -> None:
    decision = PolicyEngine().decide(
        policy(), candidate(ambiguous=True, cost=Money(currency="EUR", minor_units=0))
    )

    assert decision.verdict == PolicyVerdict.APPROVAL_REQUIRED
    assert PolicyReasonCode.AMBIGUOUS in decision.reason_codes


def test_category_ask_requires_approval_for_free_action() -> None:
    decision = PolicyEngine().decide(
        policy(calendar_mode=PolicyMode.ASK),
        candidate(
            category=ActionCategory.CALENDAR,
            cost=Money(currency="EUR", minor_units=0),
            reversible=True,
        ),
    )

    assert decision.verdict == PolicyVerdict.APPROVAL_REQUIRED
    assert decision.reason_codes == (PolicyReasonCode.CALENDAR_POLICY_ASK,)


def test_currency_mismatch_cannot_auto_authorize_spending() -> None:
    decision = PolicyEngine().decide(
        policy(), candidate(cost=Money(currency="USD", minor_units=100), reversible=True)
    )

    assert decision.verdict == PolicyVerdict.APPROVAL_REQUIRED
    assert PolicyReasonCode.UNSUPPORTED_CURRENCY in decision.reason_codes
