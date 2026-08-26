import pytest
from app.models.money import Money
from pydantic import ValidationError


def test_money_uses_minor_units_and_normalizes_currency() -> None:
    total = Money(currency="eur", minor_units=1_500).add(Money(currency="EUR", minor_units=340))

    assert total == Money(currency="EUR", minor_units=1_840)


def test_money_rejects_currency_mismatch() -> None:
    with pytest.raises(ValueError, match="currency mismatch"):
        Money(currency="EUR", minor_units=1).add(Money(currency="USD", minor_units=1))


def test_money_rejects_invalid_currency() -> None:
    with pytest.raises(ValidationError):
        Money(currency="EURO", minor_units=1)
