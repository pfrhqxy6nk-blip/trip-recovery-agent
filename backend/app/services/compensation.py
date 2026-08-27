from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any, NamedTuple

from app.models.compensation import (
    ClaimLetter,
    CompensationAssessment,
    DisruptionCategory,
    RegulationJurisdiction,
)
from app.models.domain import Incident, Trip
from app.models.money import Money


class AirportInfo(NamedTuple):
    lat: float
    lon: float
    country: str
    is_eu: bool
    is_uk: bool
    is_us: bool


# Comprehensive registry of airport coordinates and jurisdictions
AIRPORT_REGISTRY: dict[str, AirportInfo] = {
    # Poland
    "WAW": AirportInfo(52.1657, 20.9671, "PL", is_eu=True, is_uk=False, is_us=False),
    "KRK": AirportInfo(50.0777, 19.7848, "PL", is_eu=True, is_uk=False, is_us=False),
    "GDN": AirportInfo(54.3776, 18.4662, "PL", is_eu=True, is_uk=False, is_us=False),
    # Germany
    "MUC": AirportInfo(48.3537, 11.7860, "DE", is_eu=True, is_uk=False, is_us=False),
    "FRA": AirportInfo(50.0379, 8.5622, "DE", is_eu=True, is_uk=False, is_us=False),
    "BER": AirportInfo(52.3667, 13.5033, "DE", is_eu=True, is_uk=False, is_us=False),
    # Portugal & Spain
    "LIS": AirportInfo(38.7742, -9.1342, "PT", is_eu=True, is_uk=False, is_us=False),
    "OPO": AirportInfo(41.2421, -8.6786, "PT", is_eu=True, is_uk=False, is_us=False),
    "MAD": AirportInfo(40.4839, -3.5680, "ES", is_eu=True, is_uk=False, is_us=False),
    "BCN": AirportInfo(41.2974, 2.0833, "ES", is_eu=True, is_uk=False, is_us=False),
    # France & Benelux
    "CDG": AirportInfo(49.0097, 2.5479, "FR", is_eu=True, is_uk=False, is_us=False),
    "ORY": AirportInfo(48.7262, 2.3652, "FR", is_eu=True, is_uk=False, is_us=False),
    "AMS": AirportInfo(52.3105, 4.7683, "NL", is_eu=True, is_uk=False, is_us=False),
    "BRU": AirportInfo(50.9010, 4.4856, "BE", is_eu=True, is_uk=False, is_us=False),
    # Italy & Central Europe
    "FCO": AirportInfo(41.8003, 12.2389, "IT", is_eu=True, is_uk=False, is_us=False),
    "MXP": AirportInfo(45.6301, 8.7255, "IT", is_eu=True, is_uk=False, is_us=False),
    "VIE": AirportInfo(48.1103, 16.5697, "AT", is_eu=True, is_uk=False, is_us=False),
    "PRG": AirportInfo(50.1008, 14.2600, "CZ", is_eu=True, is_uk=False, is_us=False),
    "ATH": AirportInfo(37.9364, 23.9445, "GR", is_eu=True, is_uk=False, is_us=False),
    "DUB": AirportInfo(53.4213, -6.2701, "IE", is_eu=True, is_uk=False, is_us=False),
    # Nordics & Switzerland
    "CPH": AirportInfo(55.6180, 12.6508, "DK", is_eu=True, is_uk=False, is_us=False),
    "ARN": AirportInfo(59.6498, 17.9238, "SE", is_eu=True, is_uk=False, is_us=False),
    "HEL": AirportInfo(60.3172, 24.9633, "FI", is_eu=True, is_uk=False, is_us=False),
    "OSL": AirportInfo(
        60.1975, 11.1004, "NO", is_eu=True, is_uk=False, is_us=False
    ),  # EEA treated as EU261
    "ZRH": AirportInfo(
        47.4582, 8.5555, "CH", is_eu=True, is_uk=False, is_us=False
    ),  # Swiss-EU agreement
    # United Kingdom
    "LHR": AirportInfo(51.4700, -0.4543, "GB", is_eu=False, is_uk=True, is_us=False),
    "LGW": AirportInfo(51.1537, -0.1821, "GB", is_eu=False, is_uk=True, is_us=False),
    "MAN": AirportInfo(53.3537, -2.2750, "GB", is_eu=False, is_uk=True, is_us=False),
    "EDI": AirportInfo(55.9508, -3.3725, "GB", is_eu=False, is_uk=True, is_us=False),
    # United States
    "JFK": AirportInfo(40.6413, -73.7781, "US", is_eu=False, is_uk=False, is_us=True),
    "EWR": AirportInfo(40.6895, -74.1745, "US", is_eu=False, is_uk=False, is_us=True),
    "BOS": AirportInfo(42.3656, -71.0096, "US", is_eu=False, is_uk=False, is_us=True),
    "ORD": AirportInfo(41.9742, -87.9073, "US", is_eu=False, is_uk=False, is_us=True),
    "LAX": AirportInfo(33.9416, -118.4085, "US", is_eu=False, is_uk=False, is_us=True),
    "SFO": AirportInfo(37.6213, -122.3790, "US", is_eu=False, is_uk=False, is_us=True),
    "MIA": AirportInfo(25.7959, -80.2870, "US", is_eu=False, is_uk=False, is_us=True),
    # International Hubs
    "DXB": AirportInfo(25.2532, 55.3657, "AE", is_eu=False, is_uk=False, is_us=False),
    "DOH": AirportInfo(25.2609, 51.5651, "QA", is_eu=False, is_uk=False, is_us=False),
    "IST": AirportInfo(41.2753, 28.7519, "TR", is_eu=False, is_uk=False, is_us=False),
    "NRT": AirportInfo(35.7720, 140.3929, "JP", is_eu=False, is_uk=False, is_us=False),
}


class AirlineInfo(NamedTuple):
    name: str
    is_eu_carrier: bool
    is_uk_carrier: bool
    is_us_carrier: bool


AIRLINE_REGISTRY: dict[str, AirlineInfo] = {
    "LO": AirlineInfo(
        "LOT Polish Airlines", is_eu_carrier=True, is_uk_carrier=False, is_us_carrier=False
    ),
    "LH": AirlineInfo("Lufthansa", is_eu_carrier=True, is_uk_carrier=False, is_us_carrier=False),
    "AF": AirlineInfo("Air France", is_eu_carrier=True, is_uk_carrier=False, is_us_carrier=False),
    "KL": AirlineInfo(
        "KLM Royal Dutch Airlines", is_eu_carrier=True, is_uk_carrier=False, is_us_carrier=False
    ),
    "TP": AirlineInfo(
        "TAP Air Portugal", is_eu_carrier=True, is_uk_carrier=False, is_us_carrier=False
    ),
    "IB": AirlineInfo("Iberia", is_eu_carrier=True, is_uk_carrier=False, is_us_carrier=False),
    "FR": AirlineInfo("Ryanair", is_eu_carrier=True, is_uk_carrier=False, is_us_carrier=False),
    "W6": AirlineInfo("Wizz Air", is_eu_carrier=True, is_uk_carrier=False, is_us_carrier=False),
    "LX": AirlineInfo(
        "Swiss International Air Lines",
        is_eu_carrier=True,
        is_uk_carrier=False,
        is_us_carrier=False,
    ),
    "OS": AirlineInfo(
        "Austrian Airlines", is_eu_carrier=True, is_uk_carrier=False, is_us_carrier=False
    ),
    "SN": AirlineInfo(
        "Brussels Airlines", is_eu_carrier=True, is_uk_carrier=False, is_us_carrier=False
    ),
    "SK": AirlineInfo(
        "SAS Scandinavian Airlines", is_eu_carrier=True, is_uk_carrier=False, is_us_carrier=False
    ),
    "AY": AirlineInfo("Finnair", is_eu_carrier=True, is_uk_carrier=False, is_us_carrier=False),
    "EI": AirlineInfo("Aer Lingus", is_eu_carrier=True, is_uk_carrier=False, is_us_carrier=False),
    "AZ": AirlineInfo("ITA Airways", is_eu_carrier=True, is_uk_carrier=False, is_us_carrier=False),
    # UK Carriers
    "BA": AirlineInfo(
        "British Airways", is_eu_carrier=False, is_uk_carrier=True, is_us_carrier=False
    ),
    "U2": AirlineInfo("easyJet UK", is_eu_carrier=False, is_uk_carrier=True, is_us_carrier=False),
    "VS": AirlineInfo(
        "Virgin Atlantic", is_eu_carrier=False, is_uk_carrier=True, is_us_carrier=False
    ),
    # US Carriers
    "AA": AirlineInfo(
        "American Airlines", is_eu_carrier=False, is_uk_carrier=False, is_us_carrier=True
    ),
    "DL": AirlineInfo(
        "Delta Air Lines", is_eu_carrier=False, is_uk_carrier=False, is_us_carrier=True
    ),
    "UA": AirlineInfo(
        "United Airlines", is_eu_carrier=False, is_uk_carrier=False, is_us_carrier=True
    ),
    # Non-EU
    "EK": AirlineInfo("Emirates", is_eu_carrier=False, is_uk_carrier=False, is_us_carrier=False),
    "QR": AirlineInfo(
        "Qatar Airways", is_eu_carrier=False, is_uk_carrier=False, is_us_carrier=False
    ),
    "TK": AirlineInfo(
        "Turkish Airlines", is_eu_carrier=False, is_uk_carrier=False, is_us_carrier=False
    ),
}


def calculate_haversine_distance(origin: str, destination: str) -> int:
    """Calculate Great-Circle distance in kilometers between two IATA airports."""
    orig = AIRPORT_REGISTRY.get(origin.upper())
    dest = AIRPORT_REGISTRY.get(destination.upper())

    if orig is None or dest is None:
        # Fallback default distance for unknown coordinates
        return 1200

    earth_radius_km = 6371.0
    lat1, lon1 = math.radians(orig.lat), math.radians(orig.lon)
    lat2, lon2 = math.radians(dest.lat), math.radians(dest.lon)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = math.sin(dlat / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    c = 2.0 * math.asin(math.sqrt(a))

    return round(earth_radius_km * c)


def extract_airline_code(flight_number: str) -> str:
    """Extract the 2-letter airline code from a flight number (e.g. LO351 -> LO, BA123 -> BA)."""
    cleaned = flight_number.strip().upper().replace(" ", "")
    code = ""
    for char in cleaned:
        if char.isalpha():
            code += char
        else:
            break
    return code[:2] if len(code) >= 2 else cleaned[:2]


class PassengerCompensationService:
    """Calculates passenger compensation under EU261/UK261/US-DOT and generates claim packages."""

    @classmethod
    def assess_flight_disruption(
        cls,
        flight_number: str,
        origin: str,
        destination: str,
        delay_minutes: int,
        disruption_category: DisruptionCategory = DisruptionCategory.FLIGHT_DELAY,
        airline_name: str | None = None,
        extraordinary_circumstances: bool = False,
        source_links: list[str] | None = None,
        source_timestamps: list[datetime] | None = None,
    ) -> CompensationAssessment:
        origin_code = origin.strip().upper()
        dest_code = destination.strip().upper()
        carrier_code = extract_airline_code(flight_number)

        airline = AIRLINE_REGISTRY.get(carrier_code)
        resolved_airline_name = airline_name or (
            airline.name if airline else f"Airline {carrier_code}"
        )
        is_eu_carrier = airline.is_eu_carrier if airline else False
        is_uk_carrier = airline.is_uk_carrier if airline else False

        orig_info = AIRPORT_REGISTRY.get(origin_code)
        dest_info = AIRPORT_REGISTRY.get(dest_code)

        orig_is_eu = orig_info.is_eu if orig_info else False
        dest_is_eu = dest_info.is_eu if dest_info else False
        orig_is_uk = orig_info.is_uk if orig_info else False
        dest_is_uk = dest_info.is_uk if dest_info else False

        distance_km = calculate_haversine_distance(origin_code, dest_code)
        reasons: list[str] = []
        citations: list[str] = []

        # Determine jurisdiction
        is_eu261_applicable = orig_is_eu or (dest_is_eu and is_eu_carrier)
        is_uk261_applicable = orig_is_uk or (dest_is_uk and is_uk_carrier)
        normalized_source_links = list(source_links or [])
        normalized_source_timestamps = list(source_timestamps or [])

        # Threshold check: delay >= 180 min or cancellation/denied boarding
        qualifies_for_statutory_payment = (
            disruption_category
            in (DisruptionCategory.FLIGHT_CANCELLATION, DisruptionCategory.DENIED_BOARDING)
            or delay_minutes >= 180
        )

        # EU/UK fixed compensation is not payable when the carrier proves an
        # extraordinary circumstance. Keep the jurisdiction visible, but never
        # turn an uncertain cause into an automatic claim.
        if extraordinary_circumstances and qualifies_for_statutory_payment:
            if is_eu261_applicable or is_uk261_applicable:
                jurisdiction = (
                    RegulationJurisdiction.EU261
                    if is_eu261_applicable
                    else RegulationJurisdiction.UK261
                )
                citations.append(
                    "EU/UK261 extraordinary-circumstances exception; carrier evidence required"
                )
                reasons.append(
                    "The disruption may be extraordinary; compensation is held for human review."
                )
                return CompensationAssessment(
                    eligible=False,
                    jurisdiction=jurisdiction,
                    amount=None,
                    distance_km=distance_km,
                    delay_minutes=delay_minutes,
                    disruption_category=disruption_category,
                    origin=origin_code,
                    destination=dest_code,
                    airline_code=carrier_code,
                    airline_name=resolved_airline_name,
                    reasons=reasons,
                    legal_citations=citations,
                    source_links=normalized_source_links,
                    source_timestamps=normalized_source_timestamps,
                    claim_ready=False,
                )

        if is_eu261_applicable and qualifies_for_statutory_payment:
            jurisdiction = RegulationJurisdiction.EU261
            citations.append("Regulation (EC) No 261/2004, Article 7")
            citations.append("CJEU Joined Cases C-402/07 and C-432/07 (Sturgeon v Condor)")

            if distance_km <= 1500:
                amount = Money(currency="EUR", minor_units=25_000)  # €250
                reasons.append(
                    f"Flight distance ({distance_km} km) is <= 1500 km -> €250 compensation."
                )
            elif (orig_is_eu and dest_is_eu) or distance_km <= 3500:
                amount = Money(currency="EUR", minor_units=40_000)  # €400
                reasons.append(
                    f"Flight distance ({distance_km} km) is 1500-3500 km (or intra-EU) -> €400."
                )
            else:
                amount = Money(currency="EUR", minor_units=60_000)  # €600
                reasons.append(f"Extra-EU long-haul flight ({distance_km} km) > 3500 km -> €600.")

            if disruption_category == DisruptionCategory.FLIGHT_DELAY:
                reasons.append(
                    f"Delay of {delay_minutes} minutes reaches the 3-hour statutory threshold."
                )
            else:
                category_label = disruption_category.value.replace("_", " ").capitalize()
                reasons.append(
                    f"{category_label} is a statutory payment trigger; final eligibility "
                    "still requires cause and evidence review."
                )
            return CompensationAssessment(
                eligible=True,
                jurisdiction=jurisdiction,
                amount=amount,
                distance_km=distance_km,
                delay_minutes=delay_minutes,
                disruption_category=disruption_category,
                origin=origin_code,
                destination=dest_code,
                airline_code=carrier_code,
                airline_name=resolved_airline_name,
                reasons=reasons,
                legal_citations=citations,
                source_links=normalized_source_links,
                source_timestamps=normalized_source_timestamps,
                claim_ready=True,
            )

        if is_uk261_applicable and qualifies_for_statutory_payment:
            jurisdiction = RegulationJurisdiction.UK261
            citations.append(
                "The Air Passenger Rights Regulations 2019 (UK statutory equivalent to EC 261/2004)"
            )

            if distance_km <= 1500:
                amount = Money(currency="GBP", minor_units=22_000)  # £220
                reasons.append(
                    f"UK flight distance ({distance_km} km) is <= 1500 km -> £220 compensation."
                )
            elif distance_km <= 3500:
                amount = Money(currency="GBP", minor_units=35_000)  # £350
                reasons.append(
                    f"UK flight distance ({distance_km} km) is 1500-3500 km -> £350 compensation."
                )
            else:
                amount = Money(currency="GBP", minor_units=52_000)  # £520
                reasons.append(
                    f"UK long-haul flight ({distance_km} km) > 3500 km -> £520 compensation."
                )

            if disruption_category == DisruptionCategory.FLIGHT_DELAY:
                reasons.append(
                    f"Delay of {delay_minutes} minutes reaches the 3-hour UK statutory threshold."
                )
            else:
                category_label = disruption_category.value.replace("_", " ").capitalize()
                reasons.append(
                    f"{category_label} is a UK statutory payment trigger; final eligibility "
                    "still requires cause and evidence review."
                )
            return CompensationAssessment(
                eligible=True,
                jurisdiction=jurisdiction,
                amount=amount,
                distance_km=distance_km,
                delay_minutes=delay_minutes,
                disruption_category=disruption_category,
                origin=origin_code,
                destination=dest_code,
                airline_code=carrier_code,
                airline_name=resolved_airline_name,
                reasons=reasons,
                legal_citations=citations,
                source_links=normalized_source_links,
                source_timestamps=normalized_source_timestamps,
                claim_ready=True,
            )

        # US DOT does not provide a universal EU-style fixed cash schedule. For
        # US routes we create a reviewable refund/rebooking request instead of
        # inventing a compensation amount.
        if (orig_info and orig_info.is_us) or (dest_info and dest_info.is_us):
            citations.append(
                "US DOT Airline Customer Service Dashboard and 14 CFR Part 260 (refund rules)"
            )
            reasons.append(
                "US DOT rules address refunds/rebooking and do not establish a fixed cash "
                "amount for this disruption."
            )
            return CompensationAssessment(
                eligible=False,
                jurisdiction=RegulationJurisdiction.US_DOT,
                amount=None,
                distance_km=distance_km,
                delay_minutes=delay_minutes,
                disruption_category=disruption_category,
                origin=origin_code,
                destination=dest_code,
                airline_code=carrier_code,
                airline_name=resolved_airline_name,
                reasons=reasons,
                legal_citations=citations,
                source_links=normalized_source_links,
                source_timestamps=normalized_source_timestamps,
                claim_ready=qualifies_for_statutory_payment,
            )

        # Not eligible for statutory cash compensation
        if not qualifies_for_statutory_payment:
            reasons.append(
                f"Delay of {delay_minutes} min is below the 180-min statutory threshold."
            )
        if not (is_eu261_applicable or is_uk261_applicable):
            reasons.append("Route and carrier are outside the jurisdiction of EU261 / UK261.")

        return CompensationAssessment(
            eligible=False,
            jurisdiction=RegulationJurisdiction.NONE,
            amount=None,
            distance_km=distance_km,
            delay_minutes=delay_minutes,
            disruption_category=disruption_category,
            origin=origin_code,
            destination=dest_code,
            airline_code=carrier_code,
            airline_name=resolved_airline_name,
            reasons=reasons,
            legal_citations=citations,
            source_links=normalized_source_links,
            source_timestamps=normalized_source_timestamps,
            claim_ready=False,
        )

    @classmethod
    def generate_claim_letter(
        cls,
        incident_id: str,
        passenger_name: str,
        flight_number: str,
        origin: str,
        destination: str,
        scheduled_arrival: datetime,
        actual_arrival: datetime,
        booking_reference: str | None = None,
        assessment: CompensationAssessment | None = None,
        source_links: list[str] | None = None,
        evidence_timestamps: list[datetime] | None = None,
    ) -> ClaimLetter:
        delay_minutes = max(0, int((actual_arrival - scheduled_arrival).total_seconds() / 60))
        eval_assessment = assessment or cls.assess_flight_disruption(
            flight_number=flight_number,
            origin=origin,
            destination=destination,
            delay_minutes=delay_minutes,
        )
        if not eval_assessment.claim_ready:
            raise ValueError("claim draft is not ready; eligibility requires human review")

        amount_formatted = (
            f"€{eval_assessment.amount.minor_units / 100:.2f}"
            if eval_assessment.amount and eval_assessment.amount.currency == "EUR"
            else f"£{eval_assessment.amount.minor_units / 100:.2f}"
            if eval_assessment.amount and eval_assessment.amount.currency == "GBP"
            else "Statutory Amount"
        )
        is_dot = eval_assessment.jurisdiction == RegulationJurisdiction.US_DOT
        is_uk261 = eval_assessment.jurisdiction == RegulationJurisdiction.UK261
        category = eval_assessment.disruption_category
        if is_dot:
            amount_formatted = "No fixed cash amount"
        pnr_str = booking_reference or "N/A"
        date_str = scheduled_arrival.strftime("%Y-%m-%d")
        claim_id = f"CLM-{incident_id[:8].upper()}-{flight_number}"
        jurisdiction_val = eval_assessment.jurisdiction.value

        subject_en = (
            f"Compensation Claim: Flight {flight_number} on {date_str} "
            f"(PNR: {pnr_str}) - {jurisdiction_val}"
        )
        sched_str = scheduled_arrival.strftime("%Y-%m-%d %H:%M UTC")
        act_str = actual_arrival.strftime("%Y-%m-%d %H:%M UTC")
        dist_km = eval_assessment.distance_km
        if category == DisruptionCategory.FLIGHT_CANCELLATION:
            disruption_basis_en = "The flight was cancelled."
        elif category == DisruptionCategory.DENIED_BOARDING:
            disruption_basis_en = "I was denied boarding despite holding a valid booking."
        elif category == DisruptionCategory.MISSED_CONNECTION:
            disruption_basis_en = "The disruption caused a missed connection at the destination."
        else:
            disruption_basis_en = f"The flight arrived {delay_minutes} minutes late."
        if is_dot:
            body_en = (
                f"To: {eval_assessment.airline_name} Customer Relations\n"
                f"From: {passenger_name}\n\n"
                "I am requesting a review of this cancellation or significant schedule change "
                "under applicable US DOT refund and customer-service rules. Please confirm "
                "the available refund or rebooking remedy; this draft does not assert a fixed "
                "cash entitlement.\n\n"
                f"Flight {flight_number}, route {origin.upper()} to {destination.upper()}, "
                f"scheduled arrival {sched_str}, actual/updated arrival {act_str}, "
                f"delay {delay_minutes} minutes, PNR {pnr_str}.\n\n"
                "Please provide a written response and case reference.\n"
                f"Yours sincerely,\n{passenger_name}\n"
            )
        else:
            legal_reference_en = (
                "the UK261 statutory air-passenger-rights regime (Article 7)"
                if is_uk261
                else (
                    "Regulation (EC) No 261/2004 (Article 7) and CJEU Joined Cases "
                    "C-402/07 & C-432/07"
                )
            )
            body_en = (
                f"To: {eval_assessment.airline_name} Customer Relations / Legal Claims Department\n"
                f"From: {passenger_name}\n"
                f"Date: {datetime.now(UTC).strftime('%Y-%m-%d')}\n"
                f"Subject: {subject_en}\n\n"
                "Dear Claims Department,\n\n"
                "I am writing to request statutory compensation for review pursuant to "
                f"{legal_reference_en}.\n\n"
                "Flight Details:\n"
                f"- Passenger Name: {passenger_name}\n"
                f"- Booking Reference (PNR): {pnr_str}\n"
                f"- Flight Number: {flight_number}\n"
                f"- Routing: {origin.upper()} to {destination.upper()} ({dist_km} km)\n"
                f"- Scheduled Arrival: {sched_str}\n"
                f"- Actual Arrival: {act_str}\n"
                f"- Total Delay at Destination: {delay_minutes} minutes\n\n"
                f"Disruption recorded: {disruption_basis_en}\n\n"
                "Statutory Compensation Entitlement:\n"
                f"Based on the current records, the flight distance of {dist_km} km and "
                f"the recorded disruption may qualify for statutory compensation of "
                f"{amount_formatted}; "
                "please verify the final eligibility and any extraordinary circumstances.\n\n"
                "Payment Details:\n"
                f"Please transfer {amount_formatted} to my bank account within 14 "
                "calendar days.\n\n"
                "Kindly confirm receipt of this claim and provide your claim reference number.\n\n"
                f"Yours sincerely,\n{passenger_name}\n"
            )

        resolved_source_links = list(source_links or eval_assessment.source_links)
        resolved_evidence_timestamps = list(
            evidence_timestamps or eval_assessment.source_timestamps
        )
        if resolved_source_links:
            source_block = "\n\nEvidence sources:\n" + "\n".join(
                f"- {link}" for link in resolved_source_links
            )
            body_en += source_block
        if resolved_evidence_timestamps:
            timestamp_block = "\n\nEvidence timestamps (UTC):\n" + "\n".join(
                f"- {timestamp.astimezone(UTC).isoformat()}"
                for timestamp in resolved_evidence_timestamps
            )
            body_en += timestamp_block

        return ClaimLetter(
            claim_id=claim_id,
            incident_id=incident_id,
            passenger_name=passenger_name,
            airline_name=eval_assessment.airline_name,
            flight_number=flight_number,
            booking_reference=booking_reference,
            route=f"{origin.upper()} → {destination.upper()}",
            origin=origin.upper(),
            destination=destination.upper(),
            scheduled_arrival=scheduled_arrival,
            actual_arrival=actual_arrival,
            delay_minutes=delay_minutes,
            distance_km=eval_assessment.distance_km,
            compensation_amount=eval_assessment.amount or Money(currency="EUR", minor_units=0),
            jurisdiction=eval_assessment.jurisdiction,
            legal_basis=", ".join(eval_assessment.legal_citations) or "Regulation (EC) No 261/2004",
            subject_en=subject_en,
            body_en=body_en,
            # Kept as schema-compatible aliases for existing clients. The product is
            # English-first, so both legacy fields intentionally contain the same English copy.
            subject_ru=subject_en,
            body_ru=body_en,
            deadline_days=14,
            required_attachments=[
                "E-ticket receipt / Booking confirmation",
                "Boarding pass / Proof of check-in",
                "Proof of delayed arrival / Airline notification",
            ],
            source_links=resolved_source_links,
            evidence_timestamps=resolved_evidence_timestamps,
            review_required=True,
        )

    @classmethod
    def assess_incident(
        cls,
        incident: Incident,
        trip: Trip | None = None,
        passenger_name: str = "Traveler",
    ) -> tuple[CompensationAssessment, ClaimLetter | None]:
        """Assess compensation and generate a claim letter from Incident and Trip models."""
        trigger = incident.trigger
        delay_minutes = (
            incident.deterministic_impact.arrival_delta_minutes
            if incident.deterministic_impact is not None
            else max(0, int((trigger.new_arrival - trigger.old_arrival).total_seconds() / 60))
        )

        context = trigger.context if isinstance(trigger.context, dict) else {}
        origin = str(context.get("origin") or "WAW")
        destination = str(context.get("destination") or "LIS")
        booking_ref = None
        airline_name = None
        matched_flight = False

        if trip is not None and trip.items:
            for item in trip.items:
                if (
                    item.origin
                    and item.destination
                    and item.type.value == "FLIGHT"
                    and (item.external_id == trigger.flight or not matched_flight)
                ):
                    matched_flight = True
                    origin = item.origin
                    destination = item.destination
                    airline_name = item.provider
                    booking_ref = item.booking_reference
                    if item.external_id == trigger.flight:
                        break

        source_links = cls._context_source_links(context)
        source_timestamps = cls._context_timestamps(context)
        extraordinary = cls._context_bool(context, "extraordinary_circumstances")
        airline_fault = context.get("airline_fault")
        if airline_fault is False:
            extraordinary = True
        scheduled_arrival = (
            cls._context_datetime(context.get("scheduled_arrival")) or trigger.old_arrival
        )
        actual_arrival = cls._context_datetime(context.get("actual_arrival")) or trigger.new_arrival

        category = (
            DisruptionCategory.FLIGHT_CANCELLATION
            if "cancel" in trigger.type.lower()
            else DisruptionCategory.FLIGHT_DELAY
        )

        assessment = cls.assess_flight_disruption(
            flight_number=trigger.flight,
            origin=origin,
            destination=destination,
            delay_minutes=delay_minutes,
            disruption_category=category,
            airline_name=airline_name,
            extraordinary_circumstances=extraordinary,
            source_links=source_links,
            source_timestamps=source_timestamps,
        )

        # A delay/cancellation alone is not proof that the carrier is liable.  The
        # incident path is intentionally stricter than the pure statutory calculator:
        # it may create a claim only after a grounded source explicitly attributes the
        # disruption to the airline.  Missing or contradictory cause evidence is held
        # for review instead of presenting a false promise of compensation.
        if airline_fault is not True:
            reason = (
                "Airline-fault cause is not verified by an official source; compensation "
                "is held for human review."
                if airline_fault is None
                else "The source does not attribute the disruption to the airline; "
                "compensation is held for human review."
            )
            assessment = assessment.model_copy(
                update={
                    "eligible": False,
                    "amount": None,
                    "claim_ready": False,
                    "reasons": [*assessment.reasons, reason],
                }
            )

        claim_letter = None
        if assessment.claim_ready:
            claim_letter = cls.generate_claim_letter(
                incident_id=incident.incident_id,
                passenger_name=passenger_name,
                flight_number=trigger.flight,
                origin=origin,
                destination=destination,
                scheduled_arrival=scheduled_arrival,
                actual_arrival=actual_arrival,
                booking_reference=booking_ref,
                assessment=assessment,
                source_links=source_links,
                evidence_timestamps=source_timestamps,
            )

        return assessment, claim_letter

    @staticmethod
    def _context_source_links(context: dict[str, Any]) -> list[str]:
        raw = context.get("source_links", context.get("sources", []))
        values = raw if isinstance(raw, list) else [raw]
        links: list[str] = []
        for value in values:
            if isinstance(value, str) and value.strip():
                links.append(value.strip()[:2_000])
            elif isinstance(value, dict) and isinstance(value.get("url"), str):
                links.append(value["url"].strip()[:2_000])
        return list(dict.fromkeys(links))[:12]

    @staticmethod
    def _context_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value.astimezone(UTC) if value.tzinfo and value.utcoffset() is not None else None
        if isinstance(value, str) and value.strip():
            try:
                parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            except ValueError:
                return None
            return (
                parsed.astimezone(UTC) if parsed.tzinfo and parsed.utcoffset() is not None else None
            )
        return None

    @staticmethod
    def _context_timestamps(context: dict[str, Any]) -> list[datetime]:
        raw = context.get("source_timestamps", [])
        values = raw if isinstance(raw, list) else [raw]
        timestamps = [
            parsed
            for value in values
            if (parsed := PassengerCompensationService._context_datetime(value))
        ]
        return timestamps[:12]

    @staticmethod
    def _context_bool(context: dict[str, Any], key: str) -> bool:
        value = context.get(key)
        return value is True or (
            isinstance(value, str) and value.strip().lower() in {"1", "true", "yes"}
        )
