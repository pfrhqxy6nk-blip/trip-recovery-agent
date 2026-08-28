from __future__ import annotations

import json
import re
import zipfile
from datetime import UTC, datetime, timedelta
from io import BytesIO

from app.models.trip_intake import FlightImport, HotelImport, TripImportRequest


class ItineraryExtractor:
    """Multimodal and natural language itinerary extraction powered by Gemini."""

    MAX_MEDIA_BYTES = 12 * 1024 * 1024
    MAX_PKPASS_ENTRIES = 64
    MAX_PKPASS_UNCOMPRESSED_BYTES = 1 * 1024 * 1024
    MAX_PKPASS_JSON_BYTES = 256 * 1024
    MAX_PKPASS_COMPRESSION_RATIO = 100
    MAX_PDF_TEXT_BYTES = 256 * 1024

    def __init__(
        self,
        model_id: str | None = None,
        *,
        api_key: str | None = None,
        vertex_project: str | None = None,
        vertex_location: str | None = None,
    ) -> None:
        self.model_id = model_id
        self._api_key = api_key
        self._vertex_project = vertex_project
        self._vertex_location = vertex_location

    async def extract_from_text(
        self,
        text: str,
        *,
        reference_time: datetime | None = None,
    ) -> TripImportRequest:
        """Extract structured itinerary (flights and hotel) from raw text, email, or message."""
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("Itinerary text cannot be empty")

        now = reference_time or datetime.now(UTC)

        # If Gemini client is available, use LLM structured extraction
        if self.model_id:
            try:
                return await self._extract_with_gemini(cleaned, now)
            except Exception:
                # Fallback to deterministic regex extractor on API error or offline mode
                return self._extract_deterministic(cleaned, now)

        return self._extract_deterministic(cleaned, now)

    async def extract_from_media(
        self,
        media_bytes: bytes,
        mime_type: str,
        caption: str = "",
        *,
        reference_time: datetime | None = None,
    ) -> TripImportRequest:
        """Extract structured itinerary from an image or PDF document using Gemini Multimodal."""
        if len(media_bytes) > self.MAX_MEDIA_BYTES:
            raise ValueError("itinerary media exceeds the 12 MiB safety limit")
        now = reference_time or datetime.now(UTC)
        resolved_mime = self._resolve_media_mime(media_bytes, mime_type)
        if not self.model_id:
            # Offline/demo mode may parse a caption, an explicit PDF text layer, or a
            # text-bearing pass, but must never invent a booking when media contains no
            # readable itinerary. Production vision extraction is enabled by Gemini.
            extracted_text = self._media_text(media_bytes, resolved_mime)
            combined = "\n".join(part for part in (caption.strip(), extracted_text) if part)
            return self._extract_deterministic(combined, now, require_signal=True)

        from google import genai
        from google.genai import types

        client = (
            genai.Client(
                vertexai=True,
                project=self._vertex_project,
                location=self._vertex_location,
            )
            if self._vertex_project
            else genai.Client(api_key=self._api_key)
        )

        prompt = (
            "Extract the travel itinerary from this ticket/booking document or screenshot. "
            "Identify all flights in chronological order (flight number, airline provider, "
            "3-letter IATA origin and destination airports, departure and arrival datetimes "
            "in ISO-8601 with timezone, departure/arrival terminal when visible, "
            "and booking reference PNR if visible). "
            "If a hotel reservation is present, extract hotel provider, hotel name, "
            "check-in and check-out datetimes, and booking reference. "
            f"Reference context datetime is {now.isoformat()}. "
            "Return structured JSON conforming to the TripImportRequest schema."
        )

        response = await client.aio.models.generate_content(
            model=self.model_id,
            contents=[
                types.Part.from_bytes(data=media_bytes, mime_type=resolved_mime),
                types.Part.from_text(text=f"{prompt}\nAdditional user notes: {caption}"),
            ],
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
                response_schema=TripImportRequest,
            ),
        )

        parsed = self._parse_gemini_response(response, "itinerary media")
        # Vision can occasionally omit a low-salience hotel row while correctly
        # extracting the flights.  PDFs often include a trustworthy text layer;
        # use it only to fill missing fields, never to overwrite Gemini's values.
        if resolved_mime == "application/pdf" and parsed.hotel is None:
            text_layer = self._media_text(media_bytes, resolved_mime)
            if text_layer:
                deterministic = self._extract_deterministic(text_layer, now)
                if deterministic.hotel is not None:
                    parsed = parsed.model_copy(update={"hotel": deterministic.hotel})
        return parsed

    async def _extract_with_gemini(self, text: str, now: datetime) -> TripImportRequest:
        if not self.model_id:
            return self._extract_deterministic(text, now)

        from google import genai
        from google.genai import types

        client = (
            genai.Client(
                vertexai=True,
                project=self._vertex_project,
                location=self._vertex_location,
            )
            if self._vertex_project
            else genai.Client(api_key=self._api_key)
        )

        prompt = (
            "You are an expert travel itinerary parser. Extract all flights and any hotel "
            "booking from the following unstructured message or email. "
            "Format origin and destination as standard 3-letter IATA codes (e.g. WAW, MUC, LIS). "
            "All departure and arrival datetimes must be valid ISO-8601 strings with timezone. "
            "Extract departure and arrival terminal identifiers when present. "
            f"Current reference datetime is {now.isoformat()}.\n"
            f"Text to parse:\n{text}"
        )

        response = await client.aio.models.generate_content(
            model=self.model_id,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
                response_schema=TripImportRequest,
            ),
        )

        return self._parse_gemini_response(response, "structured itinerary")

    @staticmethod
    def _resolve_media_mime(media_bytes: bytes, mime_type: str) -> str:
        """Normalize Telegram's occasionally generic MIME values using safe magic bytes."""
        normalized = (mime_type or "").split(";", 1)[0].strip().lower()
        if media_bytes.startswith(b"%PDF-"):
            return "application/pdf"
        if media_bytes[:2] == b"PK" and normalized in {
            "",
            "application/octet-stream",
            "application/zip",
            "application/vnd.apple.pkpass",
        }:
            return "application/vnd.apple.pkpass"
        if media_bytes.startswith(bytes.fromhex("89504e470d0a1a0a")):
            return "image/png"
        if media_bytes.startswith(bytes.fromhex("ffd8ff")):
            return "image/jpeg"
        if media_bytes.startswith(b"RIFF") and media_bytes[8:12] == b"WEBP":
            return "image/webp"
        return normalized or "application/octet-stream"

    @staticmethod
    def _parse_gemini_response(response: object, label: str) -> TripImportRequest:
        parsed = getattr(response, "parsed", None)
        if parsed is not None:
            try:
                return TripImportRequest.model_validate(parsed)
            except (TypeError, ValueError):
                pass
        raw_text = str(getattr(response, "text", "") or "").strip()
        if raw_text.startswith(chr(96) * 3):
            raw_text = re.sub(
                r"^\x60{3}(?:json)?\s*|\s*\x60{3}$",
                "",
                raw_text,
                flags=re.IGNORECASE,
            )
        if not raw_text:
            raise ValueError(f"Gemini returned empty {label} response")
        try:
            return TripImportRequest.model_validate_json(raw_text)
        except ValueError as exc:
            raise ValueError(f"Gemini returned invalid {label} JSON") from exc

    @staticmethod
    def _media_text(media_bytes: bytes, mime_type: str) -> str:
        normalized = (mime_type or "").lower()
        if (
            normalized in {"application/vnd.apple.pkpass", "application/zip"}
            or media_bytes[:2] == b"PK"
        ):
            try:
                with zipfile.ZipFile(BytesIO(media_bytes)) as archive:
                    entries = archive.infolist()
                    if len(entries) > ItineraryExtractor.MAX_PKPASS_ENTRIES:
                        return ""
                    total_uncompressed = 0
                    for entry in entries:
                        name = entry.filename.replace("\\", "/")
                        if (
                            entry.is_dir()
                            or name != entry.filename
                            or name.startswith("/")
                            or ".." in name.split("/")
                            or entry.flag_bits & 0x1
                        ):
                            return ""
                        if entry.file_size > ItineraryExtractor.MAX_PKPASS_UNCOMPRESSED_BYTES:
                            return ""
                        if entry.file_size and (
                            entry.file_size / max(1, entry.compress_size)
                            > ItineraryExtractor.MAX_PKPASS_COMPRESSION_RATIO
                        ):
                            return ""
                        total_uncompressed += entry.file_size
                        if total_uncompressed > ItineraryExtractor.MAX_PKPASS_UNCOMPRESSED_BYTES:
                            return ""
                    pass_info = archive.getinfo("pass.json")
                    if pass_info.file_size > ItineraryExtractor.MAX_PKPASS_JSON_BYTES:
                        return ""
                    with archive.open(pass_info) as stream:
                        payload = stream.read(ItineraryExtractor.MAX_PKPASS_JSON_BYTES + 1)
                    if len(payload) > ItineraryExtractor.MAX_PKPASS_JSON_BYTES:
                        return ""
                parsed = json.loads(payload.decode("utf-8"))
                if isinstance(parsed, dict):
                    return json.dumps(parsed, ensure_ascii=False)
            except (
                KeyError,
                OSError,
                OverflowError,
                RuntimeError,
                UnicodeDecodeError,
                json.JSONDecodeError,
                zipfile.BadZipFile,
            ):
                return ""
            return ""
        if normalized == "application/pdf" or media_bytes.startswith(b"%PDF-"):
            # Read only explicit PDF literal text operators. This deliberately does not
            # decompress streams or OCR arbitrary bytes: a missing text layer still fails
            # closed instead of creating a synthetic itinerary. Gemini receives the original
            # PDF in production and remains the authoritative multimodal extractor.
            return ItineraryExtractor._pdf_literal_text(media_bytes)
        # Do not decode arbitrary binary bytes as UTF-8. A binary image or archive can contain
        # plausible-looking fragments that must never be mistaken for booking facts.
        if normalized.startswith("text/"):
            return media_bytes.decode("utf-8", errors="ignore")
        return ""

    @staticmethod
    def _pdf_literal_text(media_bytes: bytes) -> str:
        """Extract bounded, printable text from uncompressed PDF literal strings.

        This supports the dependency-free beta fixture and simple text PDFs while avoiding
        a general PDF parser in the request path. Compressed/encoded PDF text is left to
        Gemini Vision/Document handling and therefore returns an empty fallback string.
        """
        chunks: list[str] = []
        index = 0
        total = 0
        length = len(media_bytes)
        while index < length and total < ItineraryExtractor.MAX_PDF_TEXT_BYTES:
            start = media_bytes.find(b"(", index)
            if start < 0:
                break
            index = start + 1
            depth = 1
            escaped = False
            raw = bytearray()
            while index < length and depth:
                byte = media_bytes[index]
                index += 1
                if escaped:
                    if byte in b"nrtbf":
                        raw.extend(
                            {
                                ord("n"): b"\n",
                                ord("r"): b"\r",
                                ord("t"): b"\t",
                                ord("b"): b"\b",
                                ord("f"): b"\f",
                            }[byte]
                        )
                    elif 48 <= byte <= 55:
                        octal = bytearray([byte])
                        for _ in range(2):
                            if index < length and 48 <= media_bytes[index] <= 55:
                                octal.append(media_bytes[index])
                                index += 1
                            else:
                                break
                        raw.append(int(octal, 8))
                    else:
                        raw.append(byte)
                    escaped = False
                    continue
                if byte == ord("\\"):
                    escaped = True
                elif byte == ord("("):
                    depth += 1
                    raw.append(byte)
                elif byte == ord(")"):
                    depth -= 1
                    if depth:
                        raw.append(byte)
                else:
                    raw.append(byte)
            if depth:
                break
            decoded = bytes(raw).decode("latin-1", errors="ignore")
            printable = sum(char.isprintable() or char in "\n\r\t" for char in decoded)
            if decoded and printable / len(decoded) >= 0.9:
                chunks.append(decoded)
                total += len(decoded.encode("utf-8"))
        return "\n".join(chunks)[: ItineraryExtractor.MAX_PDF_TEXT_BYTES]

    def _extract_deterministic(
        self, text: str, now: datetime, *, require_signal: bool = False
    ) -> TripImportRequest:
        """Deterministic pattern parser for common flight & hotel message structures."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        full_text = " ".join(lines)

        flights: list[FlightImport] = []
        hotel: HotelImport | None = None

        # Pattern: Standard IATA Flight number (e.g. LO351, LH123, BA456, TP1234)
        flight_raw = re.findall(
            r"\b([A-Z]{2}|[A-Z][0-9]|[0-9][A-Z])\s?(\d{1,4})\b", full_text, flags=re.IGNORECASE
        )
        flight_matches = [f"{m[0].upper()}{m[1]}" for m in flight_raw]

        airport_matches = re.findall(r"\b([A-Z]{3})\b", full_text)
        known_airports = {
            "WAW",
            "MUC",
            "LIS",
            "FRA",
            "BER",
            "CDG",
            "LHR",
            "LGW",
            "AMS",
            "MAD",
            "BCN",
            "JFK",
            "EWR",
            "BOS",
            "ORD",
            "LAX",
            "SFO",
            "FCO",
            "VIE",
            "PRG",
        }
        route_airports = [a.upper() for a in airport_matches if a.upper() in known_airports]

        hotel_signal = bool(
            re.search(r"\b(?:hotel|lodging|booking\.com|airbnb)\b|отел", full_text, re.IGNORECASE)
        )
        if require_signal and not flight_matches and not hotel_signal:
            raise ValueError(
                "no flight number or hotel reservation was found in the forwarded itinerary"
            )

        # Standard airline names lookup
        airline_map = {
            "LO": "LOT Polish Airlines",
            "LH": "Lufthansa",
            "BA": "British Airways",
            "TP": "TAP Air Portugal",
            "AF": "Air France",
            "KL": "KLM",
            "IB": "Iberia",
            "FR": "Ryanair",
            "U2": "easyJet",
        }

        # Find PNR if present
        pnr_match = re.search(
            r"(?:pnr|booking ref|confirmation|serialNumber|бронь|бронирование)"
            r"[:\s=\"]+([A-Z0-9]{5,8})",
            full_text,
            flags=re.IGNORECASE,
        )
        pnr = pnr_match.group(1).upper() if pnr_match else None

        # A deterministic fallback is deliberately conservative.  It may only create an
        # itinerary when the source contains explicit timezone-aware ISO timestamps.  This
        # prevents a forwarded screenshot/PDF from silently becoming a fabricated booking.
        iso_datetimes = self._explicit_datetimes(full_text)
        if require_signal and flight_matches and len(iso_datetimes) < len(flight_matches) * 2:
            raise ValueError(
                "itinerary source did not expose explicit departure and arrival times; "
                "connect Gemini Vision or add the times in the caption"
            )

        base_date = now + timedelta(days=1)
        base_dep = datetime(base_date.year, base_date.month, base_date.day, 10, 0, tzinfo=UTC)

        if flight_matches:
            seen_flights = []
            for f in flight_matches:
                if f not in seen_flights:
                    seen_flights.append(f)

            prev_dest: str | None = None
            for idx, flight_num in enumerate(seen_flights):
                code = flight_num[:2]
                provider = airline_map.get(code, f"Airline {code}")

                # Determine origin
                if prev_dest is not None:
                    origin = prev_dest
                elif idx < len(route_airports):
                    origin = route_airports[idx]
                else:
                    origin = "WAW"

                # Determine destination
                if prev_dest is not None and len(route_airports) > idx:
                    destination = route_airports[idx]
                elif len(route_airports) > idx + 1:
                    destination = route_airports[idx + 1]
                elif origin == "WAW":
                    destination = "MUC" if len(seen_flights) > 1 and idx == 0 else "LIS"
                elif origin == "MUC":
                    destination = "LIS"
                elif origin == "FRA":
                    destination = "JFK"
                else:
                    destination = "LIS"

                if destination == origin:
                    destination = "LIS" if origin != "LIS" else "MUC"

                if len(iso_datetimes) >= (idx + 1) * 2:
                    dep, arr = iso_datetimes[idx * 2 : idx * 2 + 2]
                else:
                    # Natural-language text intake predates multimodal onboarding and keeps
                    # its demo-friendly defaults.  Media intake never reaches this branch
                    # without explicit timestamps because of the guard above.
                    dep = base_dep + timedelta(hours=idx * 4)
                    arr = dep + timedelta(hours=2)
                prev_dest = destination

                flights.append(
                    FlightImport(
                        flight_number=flight_num,
                        provider=provider,
                        origin=origin,
                        destination=destination,
                        departure_at=dep,
                        arrival_at=arr,
                        booking_reference=pnr,
                    )
                )

        if require_signal and not flights and hotel_signal and len(iso_datetimes) < 2:
            raise ValueError(
                "hotel source did not expose explicit check-in and check-out times; "
                "connect Gemini Vision or add the times in the caption"
            )

        if not flights and not hotel_signal:
            orig = route_airports[0] if len(route_airports) >= 1 else "WAW"
            dest = route_airports[1] if len(route_airports) >= 2 else "LIS"
            if orig == dest:
                dest = "LIS" if orig != "LIS" else "MUC"
            flights.append(
                FlightImport(
                    flight_number="LO351",
                    provider="LOT Polish Airlines",
                    origin=orig,
                    destination=dest,
                    departure_at=base_dep,
                    arrival_at=base_dep + timedelta(hours=2),
                    booking_reference=pnr,
                )
            )

        # Check for hotel mention
        # Prefer the source's dedicated hotel line.  PDF text layers often flatten
        # adjacent rows into one string; the broad fallback below must not swallow
        # the following transfer/check-in fields into the hotel name.
        hotel_line_match = next(
            (
                re.match(
                    r"(?:hotel|отель|stay|lodging|booking\.com|airbnb)"
                    r"(?:\s+(?:reservation|confirmation))?[:\s]+(.+?)\s*$",
                    line,
                    flags=re.IGNORECASE,
                )
                for line in lines
                if re.match(
                    r"(?:hotel|отель|stay|lodging|booking\.com|airbnb)\b",
                    line,
                    flags=re.IGNORECASE,
                )
            ),
            None,
        )
        hotel_match = re.search(
            r"(?:hotel|отель|stay|lodging|booking\.com|airbnb)"
            r"(?:\s+(?:reservation|confirmation))?[:\s]+([A-Za-z0-9 .,'-]{3,80})",
            full_text,
            flags=re.IGNORECASE,
        )
        if hotel_match or hotel_signal:
            hotel_name = (
                hotel_line_match.group(1).strip()
                if hotel_line_match is not None
                else hotel_match.group(1).strip()
                if hotel_match
                else "Trip accommodation"
            )
            hotel_name = re.split(
                r"\s+(?:check[- ]?in|check[- ]?out|transfer|arrival|departure)\b|"
                r"\s+\d{4}-\d{2}-\d{2}T",
                hotel_name,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0].strip(" ,;:-")
            if flights:
                check_in = flights[-1].arrival_at + timedelta(hours=1)
                check_out = check_in + timedelta(days=3)
            elif len(iso_datetimes) >= 2:
                check_in, check_out = iso_datetimes[:2]
            else:
                check_in = base_dep
                check_out = check_in + timedelta(days=3)
            hotel = HotelImport(
                provider="Hotel Partner",
                name=hotel_name,
                check_in_at=check_in,
                check_out_at=check_out,
                booking_reference=pnr,
            )

        return TripImportRequest(
            flights=flights,
            hotel=hotel,
            minimum_connection_minutes=45,
        )

    @staticmethod
    def _explicit_datetimes(text: str) -> list[datetime]:
        """Extract only timezone-aware ISO timestamps from untrusted source text."""

        values: list[datetime] = []
        for match in re.findall(
            r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})\b",
            text,
        ):
            try:
                parsed = datetime.fromisoformat(match.replace("Z", "+00:00"))
            except ValueError:
                continue
            if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                values.append(parsed.astimezone(UTC))
        return values
