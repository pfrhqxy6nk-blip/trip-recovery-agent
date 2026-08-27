from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Money(BaseModel):
    """Authoritative money value represented in integer minor units."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    currency: str = Field(min_length=3, max_length=3)
    minor_units: int

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        normalized = value.upper()
        if len(normalized) != 3 or not normalized.isalpha():
            raise ValueError("currency must be a three-letter ISO-style code")
        return normalized

    def _require_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise ValueError(f"currency mismatch: {self.currency} != {other.currency}")

    def add(self, other: Money) -> Money:
        self._require_same_currency(other)
        return Money(currency=self.currency, minor_units=self.minor_units + other.minor_units)

    def is_lte(self, other: Money) -> bool:
        self._require_same_currency(other)
        return self.minor_units <= other.minor_units

    def subtract(self, other: Money) -> Money:
        self._require_same_currency(other)
        return Money(currency=self.currency, minor_units=self.minor_units - other.minor_units)
