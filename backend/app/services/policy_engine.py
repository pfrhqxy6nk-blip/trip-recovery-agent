from __future__ import annotations

from app.models.enums import ActionCategory, PolicyMode, PolicyReasonCode, PolicyVerdict
from app.models.money import Money
from app.models.policy import AutonomyPolicy, PolicyCandidate, PolicyDecision


class PolicyEngine:
    """Applies deterministic authority rules in mandatory-risk-first order."""

    def decide(
        self,
        policy: AutonomyPolicy,
        candidate: PolicyCandidate,
        already_auto_authorized: Money | None = None,
    ) -> PolicyDecision:
        reasons: list[PolicyReasonCode] = []
        if candidate.ambiguous:
            reasons.append(PolicyReasonCode.AMBIGUOUS)
        if not candidate.reversible:
            reasons.append(PolicyReasonCode.IRREVERSIBLE)
        if candidate.penalty_minor_units > 0:
            reasons.append(PolicyReasonCode.PENALTY_BEARING)
        if candidate.major_change_reasons:
            reasons.append(PolicyReasonCode.MAJOR_ITINERARY_CHANGE)

        if candidate.category == ActionCategory.CALENDAR and policy.calendar_mode == PolicyMode.ASK:
            reasons.append(PolicyReasonCode.CALENDAR_POLICY_ASK)
        if (
            candidate.category == ActionCategory.SERVICE_MESSAGE
            and policy.service_message_mode == PolicyMode.ASK
        ):
            reasons.append(PolicyReasonCode.SERVICE_MESSAGE_POLICY_ASK)
        if (
            candidate.category == ActionCategory.REVERSIBLE_CHANGE
            and policy.reversible_change_mode == PolicyMode.ASK
        ):
            reasons.append(PolicyReasonCode.REVERSIBLE_CHANGE_POLICY_ASK)

        remaining = self._remaining_spend(policy, already_auto_authorized, candidate.cost)
        if candidate.cost.minor_units > 0:
            if not policy.automatic_spending_enabled:
                reasons.append(PolicyReasonCode.AUTOMATIC_SPENDING_DISABLED)
            elif remaining is None:
                reasons.append(PolicyReasonCode.UNSUPPORTED_CURRENCY)
            elif candidate.cost.minor_units > remaining.minor_units:
                reasons.append(PolicyReasonCode.SPENDING_LIMIT_EXCEEDED)

        if reasons:
            return PolicyDecision(
                action_id=candidate.action_id,
                verdict=PolicyVerdict.APPROVAL_REQUIRED,
                reason_codes=tuple(dict.fromkeys(reasons)),
                remaining_automatic_spend=remaining,
            )
        return PolicyDecision(
            action_id=candidate.action_id,
            verdict=PolicyVerdict.AUTO_APPROVED,
            reason_codes=(PolicyReasonCode.AUTO_ALLOWED,),
            remaining_automatic_spend=remaining,
        )

    @staticmethod
    def _remaining_spend(
        policy: AutonomyPolicy,
        already_auto_authorized: Money | None,
        candidate_cost: Money,
    ) -> Money | None:
        limit = policy.incident_spending_limit
        if limit is None:
            return None
        if limit.currency != candidate_cost.currency:
            return None
        spent = already_auto_authorized or Money(currency=limit.currency, minor_units=0)
        if spent.currency != limit.currency:
            return None
        return limit.subtract(spent)
