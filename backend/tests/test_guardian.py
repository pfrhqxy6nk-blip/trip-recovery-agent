from __future__ import annotations

from app.models.guardian import (
    BaggageScreeningStatus,
    TravelerTravelProfile,
    VisaScreeningStatus,
)
from app.services.guardian import TravelGuardianService


def test_intra_schengen_clear_for_eu_passport() -> None:
    report = TravelGuardianService.screen_connection(
        connection_id="conn-waw-muc-lis",
        origin_airport="WAW",
        hub_airport="MUC",
        destination_airport="LIS",
        scheduled_buffer_minutes=55,
        profile=TravelerTravelProfile(citizenship_iso2="DE", has_checked_bags=True, bag_count=1),
    )

    assert report.visa.status == VisaScreeningStatus.CLEAR
    assert report.visa.schengen_transit is True
    assert report.visa.requires_human_confirmation is False
    assert (
        report.baggage.status == BaggageScreeningStatus.TIGHT_ESTIMATE
    )  # 55m buffer vs 45m MBCT = +10m slack
    assert report.baggage.estimated_mbct_minutes == 45
    assert report.is_safe_to_reroute_estimate is True


def test_carry_on_only_removes_baggage_risk() -> None:
    report = TravelGuardianService.screen_connection(
        connection_id="conn-waw-muc-lis",
        origin_airport="WAW",
        hub_airport="MUC",
        destination_airport="LIS",
        scheduled_buffer_minutes=35,
        profile=TravelerTravelProfile(citizenship_iso2="FR", has_checked_bags=False, bag_count=0),
    )

    assert report.baggage.status == BaggageScreeningStatus.FEASIBLE_ESTIMATE
    assert report.baggage.estimated_mbct_minutes == 0
    assert report.is_safe_to_reroute_estimate is True


def test_tight_baggage_deficit_yields_high_risk() -> None:
    report = TravelGuardianService.screen_connection(
        connection_id="conn-fra-lhr-jfk",
        origin_airport="FRA",
        hub_airport="LHR",
        destination_airport="JFK",
        scheduled_buffer_minutes=50,  # LHR estimated MBCT is 75m -> deficit 25m
        profile=TravelerTravelProfile(citizenship_iso2="US", has_checked_bags=True, bag_count=2),
    )

    assert report.baggage.status == BaggageScreeningStatus.HIGH_RISK_ESTIMATE
    assert report.baggage.estimated_mbct_minutes == 75
    assert report.is_safe_to_reroute_estimate is False


def test_uk_transit_for_non_eu_us_requires_verification() -> None:
    report = TravelGuardianService.screen_connection(
        connection_id="conn-waw-lhr-jfk",
        origin_airport="WAW",
        hub_airport="LHR",
        destination_airport="JFK",
        scheduled_buffer_minutes=90,
        profile=TravelerTravelProfile(citizenship_iso2="IN", has_checked_bags=False),
    )

    assert report.visa.status == VisaScreeningStatus.REQUIRES_VERIFICATION
    assert report.visa.requires_human_confirmation is True
    assert "DATV" in report.visa.notes[0]
    assert report.is_safe_to_reroute_estimate is False


def test_us_hub_requires_customs_and_visa_verification() -> None:
    report = TravelGuardianService.screen_connection(
        connection_id="conn-fra-jfk-lax",
        origin_airport="FRA",
        hub_airport="JFK",
        destination_airport="LAX",
        scheduled_buffer_minutes=120,
        profile=TravelerTravelProfile(citizenship_iso2="UA", has_checked_bags=True),
    )

    assert report.visa.status == VisaScreeningStatus.REQUIRES_VERIFICATION
    assert report.baggage.requires_customs_recheck is True
    assert report.baggage.estimated_mbct_minutes == 90
    assert report.is_safe_to_reroute_estimate is False


def test_unknown_citizenship_returns_unknown_and_disclaimer() -> None:
    report = TravelGuardianService.screen_connection(
        connection_id="conn-custom",
        origin_airport="MUC",
        hub_airport="FRA",
        destination_airport="JFK",
        scheduled_buffer_minutes=60,
        profile=TravelerTravelProfile(citizenship_iso2="ZZ", has_checked_bags=True),
    )

    assert report.visa.status == VisaScreeningStatus.UNKNOWN
    assert report.visa.requires_human_confirmation is True
    assert "Screening tool only" in report.visa.legal_disclaimer
