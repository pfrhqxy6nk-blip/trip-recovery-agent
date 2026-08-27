from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.domain import _require_aware

DocumentKind = Literal[
    "FLIGHT_TICKET",
    "BOARDING_PASS",
    "HOTEL_CONFIRMATION",
    "TRANSFER_VOUCHER",
    "INSURANCE",
    "OTHER",
]


class TripDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1, max_length=200)
    trip_id: str = Field(min_length=1, max_length=200)
    owner_user_id: str = Field(min_length=1, max_length=200)
    kind: DocumentKind
    linked_item_id: str | None = Field(default=None, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    source: Literal["DEMO", "GMAIL", "TELEGRAM", "MANUAL"]
    source_id: str | None = Field(default=None, max_length=128)
    mime_type: str | None = Field(default=None, max_length=120)
    created_at: datetime

    _aware_created = field_validator("created_at")(_require_aware)


class ReadinessFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: Literal["MISSING_DOCUMENT", "TIGHT_CONNECTION", "SCHEDULE_CONFLICT"]
    severity: Literal["INFO", "ATTENTION"]
    summary: str = Field(min_length=1, max_length=500)
    item_id: str | None = Field(default=None, max_length=200)


class TripReadinessReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trip_id: str
    status: Literal["READY", "NEEDS_ATTENTION"]
    documents_present: tuple[DocumentKind, ...]
    documents_missing: tuple[DocumentKind, ...]
    findings: tuple[ReadinessFinding, ...]
    generated_at: datetime

    _aware_generated = field_validator("generated_at")(_require_aware)
