#!/usr/bin/env python3
"""Build safe, clearly-labelled beta fixtures for the judge/demo flow."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "demo" / "fixtures"

EMAIL_TEXT = """TRIP WATCH BETA FIXTURE - NOT VALID FOR TRAVEL

This synthetic confirmation is for the Trip Watch judge demo only.
No ticket has been issued and no payment has been taken.

Passenger: Alex Morgan
Booking reference / PNR: TWDEMO

Flight LO351 - Warsaw (WAW) to Munich (MUC)
Departure: 2026-09-08T09:10:00+02:00
Arrival: 2026-09-08T10:45:00+02:00
Terminal: 1

Flight LH1790 - Munich (MUC) to Lisbon (LIS)
Departure: 2026-09-08T11:40:00+02:00
Arrival: 2026-09-08T13:45:00+01:00
Terminal: 2

Hotel: Hotel Torbraeu, Munich
Check-in: 2026-09-08T15:00:00+02:00
Check-out: 2026-09-12T11:00:00+02:00

Transfer: Lisbon Airport pickup, 2026-09-08T14:30:00+01:00
"""

PASS_JSON = {
    "formatVersion": 1,
    "passTypeIdentifier": "demo.tripwatch.mock",
    "serialNumber": "TWDEMO",
    "teamIdentifier": "DEMO00",
    "organizationName": "Trip Watch Beta",
    "description": "Synthetic Trip Watch boarding pass - not valid for travel",
    "boardingPass": {
        "headerFields": [{"key": "flight", "label": "Flight", "value": "LO351"}],
        "primaryFields": [{"key": "route", "label": "Route", "value": "WAW to MUC"}],
        "secondaryFields": [
            {"key": "departure", "label": "Departure", "value": "2026-09-08T09:10:00+02:00"},
            {"key": "arrival", "label": "Arrival", "value": "2026-09-08T10:45:00+02:00"},
        ],
    },
    "tripWatch": {
        "demo": True,
        "flight": "LO351",
        "origin": "WAW",
        "destination": "MUC",
        "departure_at": "2026-09-08T09:10:00+02:00",
        "arrival_at": "2026-09-08T10:45:00+02:00",
        "hotel": "Hotel Torbraeu",
        "hotel_check_in": "2026-09-08T15:00:00+02:00",
        "hotel_check_out": "2026-09-12T11:00:00+02:00",
        "booking_reference": "TWDEMO",
    },
}


def _escape_pdf(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf(path: Path) -> None:
    """Write a small, dependency-free PDF with a clean editorial layout.

    ReportLab is intentionally not required for the repository's lightweight
    demo build environment, so this uses the PDF text/graphics primitives
    directly and keeps all PDF copy ASCII-safe.
    """
    lines = [
        ("TRIP WATCH / BETA FIXTURE", 9, 770, "blue"),
        ("Synthetic booking confirmation", 22, 738, "black"),
        ("DEMO ONLY - NOT VALID FOR TRAVEL", 11, 704, "red"),
        (
            "Generated for the hackathon judge flow. No ticket, reservation, payment,",
            10,
            680,
            "gray",
        ),
        ("or identity document exists.", 10, 664, "gray"),
        ("PASSENGER     Alex Morgan", 11, 620, "black"),
        ("PNR           TWDEMO", 11, 594, "black"),
        ("OUTBOUND      LO351   WAW -> MUC", 11, 568, "black"),
        ("DEPARTURE     2026-09-08T09:10:00+02:00", 11, 542, "black"),
        ("ARRIVAL       2026-09-08T10:45:00+02:00", 11, 516, "black"),
        ("CONNECTION     LH1790   MUC -> LIS   55 min", 11, 490, "black"),
        ("DEPARTURE     2026-09-08T11:40:00+02:00", 11, 464, "black"),
        ("ARRIVAL       2026-09-08T13:45:00+01:00", 11, 438, "black"),
        ("HOTEL         Hotel Torbraeu   late-arrival watch", 11, 412, "black"),
        ("TRANSFER      Lisbon Airport pickup   2026-09-08T14:30:00+01:00", 11, 386, "black"),
        (
            "Forward this PDF to the Telegram bot. Gemini extracts the itinerary; the",
            10,
            340,
            "gray",
        ),
        ("companion text and .pkpass fixtures provide a deterministic fallback.", 10, 324, "gray"),
    ]
    color = {
        "black": (0.07, 0.07, 0.07),
        "gray": (0.30, 0.30, 0.30),
        "blue": (0.09, 0.41, 0.88),
        "red": (0.75, 0.15, 0.12),
    }
    stream: list[str] = ["q", "0.98 0.98 0.98 rg", "36 330 540 470 re", "f", "Q"]
    for text, size, y, tone in lines:
        r, g, b = color[tone]
        stream.extend(
            [
                "BT",
                f"/{'F2' if size >= 18 else 'F1'} {size} Tf",
                f"{r} {g} {b} rg",
                f"48 {y} Td",
                f"({_escape_pdf(text)}) Tj",
                "ET",
            ]
        )
    stream.extend(["0.09 0.41 0.88 RG", "48 420 m", "564 420 l", "S"])
    content = "\n".join(stream).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
        b"<< /Length "
        + str(len(content)).encode("ascii")
        + b" >>\nstream\n"
        + content
        + b"\nendstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    path.write_bytes(pdf)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "warsaw-munich-lisbon-booking-email.txt").write_text(EMAIL_TEXT, encoding="utf-8")
    build_pdf(OUT / "warsaw-munich-lisbon-booking.pdf")
    # Keep the fixture byte-for-byte reproducible. ZipFile otherwise embeds
    # the current wall-clock timestamp, which creates a noisy binary diff on
    # every regeneration and makes the judge pack harder to verify.
    pass_info = zipfile.ZipInfo("pass.json", date_time=(1980, 1, 1, 0, 0, 0))
    pass_info.create_system = 3
    pass_info.external_attr = 0o644 << 16
    pass_info.compress_type = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(OUT / "warsaw-munich-lisbon-demo.pkpass", "w") as archive:
        archive.writestr(pass_info, json.dumps(PASS_JSON, indent=2) + "\n")
    (OUT / "airport-board-delay.txt").write_text(
        "TRIP WATCH BETA FIXTURE - NOT LIVE\n"
        "Synthetic airport-board signal for the judge simulator.\n"
        "LO351 WAW to MUC - arrival delayed by 105 minutes.\n"
        "Observed: 2026-09-08T08:55:00+02:00\n",
        encoding="utf-8",
    )
    print(f"Wrote beta fixtures to {OUT}")


if __name__ == "__main__":
    main()
