from __future__ import annotations

from app.models.guardian import (
    BaggageScreeningResult,
    BaggageScreeningStatus,
    TravelerTravelProfile,
    TravelScreeningReport,
    VisaScreeningResult,
    VisaScreeningStatus,
)

# Standard Schengen Countries ISO-2
SCHENGEN_COUNTRIES = {
    "AT",
    "BE",
    "CH",
    "CZ",
    "DE",
    "DK",
    "EE",
    "ES",
    "FI",
    "FR",
    "GR",
    "HR",
    "HU",
    "IS",
    "IT",
    "LT",
    "LU",
    "LV",
    "MT",
    "NL",
    "NO",
    "PL",
    "PT",
    "SE",
    "SI",
    "SK",
}

# Airport to ISO-2 Country mapping for major hubs
AIRPORT_COUNTRY: dict[str, str] = {
    "WAW": "PL",
    "KRK": "PL",
    "GDN": "PL",
    "MUC": "DE",
    "FRA": "DE",
    "BER": "DE",
    "HAM": "DE",
    "LIS": "PT",
    "OPO": "PT",
    "FAO": "PT",
    "CDG": "FR",
    "ORY": "FR",
    "NCE": "FR",
    "AMS": "NL",
    "VIE": "AT",
    "ZRH": "CH",
    "GVA": "CH",
    "FCO": "IT",
    "MXP": "IT",
    "MAD": "ES",
    "BCN": "ES",
    "LHR": "GB",
    "LGW": "GB",
    "MAN": "GB",
    "EDI": "GB",
    "JFK": "US",
    "EWR": "US",
    "BOS": "US",
    "ORD": "US",
    "LAX": "US",
    "SFO": "US",
    "MIA": "US",
}

# Default estimated Minimum Baggage Connection Times (minutes)
# Heuristics based on airport infrastructure and terminal configurations
HUB_ESTIMATED_MBCT: dict[str, int] = {
    "MUC": 45,  # Compact terminal 2 Lufthansa hub
    "FRA": 55,  # Large inter-terminal transit
    "LHR": 75,  # Long bus / terminal transfer
    "CDG": 70,  # 2E to 2F/2G bus transit
    "AMS": 50,  # Single-terminal concept
    "ZRH": 40,  # Efficient compact hub
    "VIE": 35,  # Fast connection concept
    "WAW": 40,  # Small hub
    "LIS": 45,  # Single main commercial hub
    "JFK": 90,  # Mandatory customs luggage re-check on international arrival
    "ORD": 85,  # US Customs re-check
    "LAX": 90,  # US Customs re-check
}


class TravelGuardianService:
    """Pre-travel screening engine for transit visa boundaries and estimated baggage timing."""

    @classmethod
    def screen_connection(
        cls,
        *,
        connection_id: str,
        origin_airport: str,
        hub_airport: str,
        destination_airport: str,
        scheduled_buffer_minutes: int,
        profile: TravelerTravelProfile | None = None,
    ) -> TravelScreeningReport:
        traveler = profile or TravelerTravelProfile()
        origin_country = AIRPORT_COUNTRY.get(origin_airport.upper(), "UNKNOWN")
        hub_country = AIRPORT_COUNTRY.get(hub_airport.upper(), "UNKNOWN")
        dest_country = AIRPORT_COUNTRY.get(destination_airport.upper(), "UNKNOWN")

        visa_res = cls._screen_visa(
            citizenship=traveler.citizenship_iso2.upper(),
            origin_country=origin_country,
            hub_country=hub_country,
            dest_country=dest_country,
            hub_airport=hub_airport.upper(),
        )

        baggage_res = cls._screen_baggage(
            hub_airport=hub_airport.upper(),
            hub_country=hub_country,
            scheduled_buffer_minutes=scheduled_buffer_minutes,
            profile=traveler,
        )

        is_safe = visa_res.status == VisaScreeningStatus.CLEAR and baggage_res.status in (
            BaggageScreeningStatus.FEASIBLE_ESTIMATE,
            BaggageScreeningStatus.TIGHT_ESTIMATE,
        )

        summary_parts = []
        if visa_res.status == VisaScreeningStatus.CLEAR:
            summary_parts.append("Visa screening: clear")
        elif visa_res.status == VisaScreeningStatus.REQUIRES_VERIFICATION:
            summary_parts.append("Visa screening: requires verification")
        else:
            summary_parts.append("Visa screening: unknown nationality/route")

        if baggage_res.status == BaggageScreeningStatus.FEASIBLE_ESTIMATE:
            summary_parts.append("Baggage timing: feasible estimate")
        elif baggage_res.status == BaggageScreeningStatus.TIGHT_ESTIMATE:
            summary_parts.append("Baggage timing: tight estimate")
        else:
            summary_parts.append("Baggage timing: high risk estimate")

        return TravelScreeningReport(
            connection_id=connection_id,
            hub_airport=hub_airport.upper(),
            visa=visa_res,
            baggage=baggage_res,
            is_safe_to_reroute_estimate=is_safe,
            summary=" · ".join(summary_parts),
        )

    @classmethod
    def _screen_visa(
        cls,
        *,
        citizenship: str,
        origin_country: str,
        hub_country: str,
        dest_country: str,
        hub_airport: str,
    ) -> VisaScreeningResult:
        notes: list[str] = []

        # 1. Intra-Schengen travel
        if (
            origin_country in SCHENGEN_COUNTRIES
            and hub_country in SCHENGEN_COUNTRIES
            and dest_country in SCHENGEN_COUNTRIES
        ):
            notes.append(
                "Intra-Schengen routing: No border or immigration control between member states."
            )
            return VisaScreeningResult(
                status=VisaScreeningStatus.CLEAR,
                schengen_transit=True,
                airside_transit_allowed=True,
                requires_human_confirmation=False,
                notes=notes,
            )

        # 2. EU/EEA/CH citizens traveling within Europe/US
        if citizenship in SCHENGEN_COUNTRIES:
            notes.append(
                f"EU/Schengen passport ({citizenship}): Freedom of movement within Schengen."
            )
            return VisaScreeningResult(
                status=VisaScreeningStatus.CLEAR,
                schengen_transit=hub_country in SCHENGEN_COUNTRIES,
                airside_transit_allowed=True,
                requires_human_confirmation=False,
                notes=notes,
            )

        # 3. US Citizens transit
        if citizenship == "US":
            if hub_country in SCHENGEN_COUNTRIES or hub_country == "GB":
                notes.append("US passport: Visa-free transit for tourism/business under 90 days.")
                return VisaScreeningResult(
                    status=VisaScreeningStatus.CLEAR,
                    schengen_transit=hub_country in SCHENGEN_COUNTRIES,
                    airside_transit_allowed=True,
                    requires_human_confirmation=False,
                    notes=notes,
                )

        # 4. UK Airport Airside Transit (LHR, LGW)
        if hub_country == "GB":
            notes.append(
                f"UK Transit ({hub_airport}): Non-EU/US passports may require "
                "a Direct Airside Transit Visa (DATV). Manual airline verification required."
            )
            return VisaScreeningResult(
                status=VisaScreeningStatus.REQUIRES_VERIFICATION,
                schengen_transit=False,
                airside_transit_allowed=None,
                requires_human_confirmation=True,
                notes=notes,
            )

        # 5. US Hub transit (No sterile airside transit in the US)
        if hub_country == "US":
            notes.append(
                f"US Transit ({hub_airport}): The US has no sterile transit. All transit "
                "passengers must clear US customs/immigration (ESTA or visa required)."
            )
            return VisaScreeningResult(
                status=VisaScreeningStatus.REQUIRES_VERIFICATION,
                schengen_transit=False,
                airside_transit_allowed=False,
                requires_human_confirmation=True,
                notes=notes,
            )

        # 6. Fallback: Unknown or unlisted nationality/route
        notes.append(
            f"Screening not definitive for citizenship {citizenship} at hub {hub_airport}. "
            "Requires human verification with airline/consulate."
        )
        return VisaScreeningResult(
            status=VisaScreeningStatus.UNKNOWN,
            schengen_transit=hub_country in SCHENGEN_COUNTRIES,
            airside_transit_allowed=None,
            requires_human_confirmation=True,
            notes=notes,
        )

    @classmethod
    def _screen_baggage(
        cls,
        *,
        hub_airport: str,
        hub_country: str,
        scheduled_buffer_minutes: int,
        profile: TravelerTravelProfile,
    ) -> BaggageScreeningResult:
        notes: list[str] = []

        if not profile.has_checked_bags or profile.bag_count == 0:
            notes.append("Carry-on only: No checked baggage transfer risk.")
            return BaggageScreeningResult(
                status=BaggageScreeningStatus.FEASIBLE_ESTIMATE,
                scheduled_buffer_minutes=scheduled_buffer_minutes,
                estimated_mbct_minutes=0,
                has_checked_bags=False,
                requires_customs_recheck=False,
                notes=notes,
            )

        estimated_mbct = HUB_ESTIMATED_MBCT.get(hub_airport, 50)
        requires_customs = hub_country == "US"  # All US transit requires baggage recheck

        if requires_customs:
            notes.append(
                "US customs: Luggage must be collected and re-checked by passenger "
                "at first port of entry."
            )

        slack = scheduled_buffer_minutes - estimated_mbct

        if slack >= 20:
            status = BaggageScreeningStatus.FEASIBLE_ESTIMATE
            notes.append(
                f"Estimated MBCT: {estimated_mbct}m (scheduled buffer: "
                f"{scheduled_buffer_minutes}m, slack: +{slack}m)."
            )
        elif slack >= 0:
            status = BaggageScreeningStatus.TIGHT_ESTIMATE
            notes.append(
                f"Tight baggage buffer: estimated MBCT {estimated_mbct}m leaves only "
                f"{slack}m margin. Baggage may miss connection if feeder flight is delayed."
            )
        else:
            status = BaggageScreeningStatus.HIGH_RISK_ESTIMATE
            deficit = abs(slack)
            notes.append(
                f"High risk baggage deficit: scheduled buffer ({scheduled_buffer_minutes}m) "
                f"is {deficit}m shorter than estimated MBCT ({estimated_mbct}m). Bag delay likely."
            )

        return BaggageScreeningResult(
            status=status,
            scheduled_buffer_minutes=scheduled_buffer_minutes,
            estimated_mbct_minutes=estimated_mbct,
            has_checked_bags=True,
            requires_customs_recheck=requires_customs,
            notes=notes,
        )
