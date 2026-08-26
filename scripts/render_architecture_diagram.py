from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "architecture-diagram.png"


def font(size: int, bold: bool = False):
    candidates = [
        (
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
            if bold
            else "/System/Library/Fonts/Supplemental/Arial.ttf"
        ),
        "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def rounded(draw: ImageDraw.ImageDraw, box, fill, outline="#D8DEE8", radius=24, width=2):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def label(draw, xy, text, size=28, fill="#111827", bold=False, anchor=None):
    draw.text(xy, text, font=font(size, bold), fill=fill, anchor=anchor)


def arrow(draw, start, end, fill="#6B7280", width=5):
    draw.line([start, end], fill=fill, width=width)
    x1, y1 = start
    x2, y2 = end
    if abs(x2 - x1) >= abs(y2 - y1):
        sign = 1 if x2 >= x1 else -1
        head = [(x2, y2), (x2 - sign * 18, y2 - 12), (x2 - sign * 18, y2 + 12)]
    else:
        sign = 1 if y2 >= y1 else -1
        head = [(x2, y2), (x2 - 12, y2 - sign * 18), (x2 + 12, y2 - sign * 18)]
    draw.polygon(head, fill=fill)


def main() -> None:
    width, height = 2400, 1500
    image = Image.new("RGB", (width, height), "#FBFCFE")
    draw = ImageDraw.Draw(image)

    label(draw, (120, 90), "Trip Watch · Google Cloud architecture", size=56, bold=True)
    label(
        draw,
        (120, 165),
        "Event-driven monitoring, multimodal intake and safe autonomous recovery",
        size=28,
        fill="#5B6472",
    )

    # Primary flow
    rounded(draw, (120, 300, 500, 650), "#EAF2FF", outline="#9CC2FF")
    label(draw, (310, 365), "Traveler", size=34, bold=True, anchor="mm")
    label(draw, (310, 430), "Telegram", size=30, fill="#2476E9", bold=True, anchor="mm")
    label(draw, (310, 490), "text · PDF · screenshot", size=22, fill="#5B6472", anchor="mm")
    label(draw, (310, 530), ".pkpass · booking email", size=22, fill="#5B6472", anchor="mm")

    rounded(draw, (650, 300, 1080, 650), "#F2F5FA")
    label(draw, (865, 365), "Secure edge", size=34, bold=True, anchor="mm")
    label(draw, (865, 430), "Cloud Run · public", size=30, fill="#2476E9", bold=True, anchor="mm")
    label(draw, (865, 490), "secret + route validation", size=22, fill="#5B6472", anchor="mm")
    label(draw, (865, 530), "IAM identity token", size=22, fill="#5B6472", anchor="mm")

    rounded(draw, (1230, 300, 1770, 650), "#EEF9F2", outline="#9EDBB1")
    label(draw, (1500, 365), "Agent worker", size=34, bold=True, anchor="mm")
    label(draw, (1500, 430), "Cloud Run · private", size=30, fill="#1A9B52", bold=True, anchor="mm")
    label(draw, (1500, 490), "Google ADK + Gemini", size=22, fill="#5B6472", anchor="mm")
    label(draw, (1500, 530), "durable workflow state", size=22, fill="#5B6472", anchor="mm")

    rounded(draw, (1920, 300, 2280, 650), "#FFF6E8", outline="#F3C779")
    label(draw, (2100, 365), "Verified", size=34, bold=True, anchor="mm")
    label(draw, (2100, 430), "recovery", size=30, fill="#D88413", bold=True, anchor="mm")
    label(draw, (2100, 490), "Telegram update", size=22, fill="#5B6472", anchor="mm")
    label(draw, (2100, 530), "claim draft / audit", size=22, fill="#5B6472", anchor="mm")

    arrow(draw, (500, 475), (650, 475), fill="#2476E9")
    arrow(draw, (1080, 475), (1230, 475), fill="#2476E9")
    arrow(draw, (1770, 475), (1920, 475), fill="#2476E9")

    # Supporting services
    label(draw, (120, 805), "Persistent services", size=34, bold=True)
    cards = [
        (
            (120, 880, 650, 1170),
            "Firestore",
            "trips · policy · watchpoints\nstate · evidence · outbox",
            "#EAF2FF",
            "#2476E9",
        ),
        (
            (720, 880, 1250, 1170),
            "Pub/Sub",
            "disruption events\nretryable workflow handoff",
            "#F3ECFF",
            "#7A4BE8",
        ),
        (
            (1320, 880, 1850, 1170),
            "Scheduler",
            "periodic Trip Watch\nsource checks",
            "#EAF9F2",
            "#1A9B52",
        ),
        (
            (1920, 880, 2280, 1170),
            "Secrets",
            "Secret Manager\nTelegram + provider keys",
            "#FFF2F2",
            "#D34B4B",
        ),
    ]
    for box, title, body, fill, accent in cards:
        rounded(draw, box, fill)
        x1, y1, x2, y2 = box
        label(draw, (x1 + 32, y1 + 42), title, size=28, bold=True, fill=accent)
        label(draw, (x1 + 32, y1 + 104), body, size=22, fill="#4B5563")

    # Agent stages
    rounded(draw, (120, 1240, 2280, 1380), "#111827", outline="#111827", radius=20, width=1)
    label(draw, (160, 1300), "Trip Watch", size=25, bold=True, fill="#FFFFFF")
    label(draw, (470, 1300), "→", size=30, fill="#8AB4FF")
    label(draw, (540, 1300), "Validate source", size=25, bold=True, fill="#FFFFFF")
    label(draw, (930, 1300), "→", size=30, fill="#8AB4FF")
    label(draw, (1000, 1300), "Impact graph", size=25, bold=True, fill="#FFFFFF")
    label(draw, (1375, 1300), "→", size=30, fill="#8AB4FF")
    label(draw, (1445, 1300), "Policy-safe actions", size=25, bold=True, fill="#FFFFFF")
    label(draw, (1900, 1300), "→", size=30, fill="#8AB4FF")
    label(draw, (1970, 1300), "Verify + notify", size=25, bold=True, fill="#FFFFFF")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUT, "PNG", optimize=True)
    image.save(OUT.with_suffix(".pdf"), "PDF", resolution=150.0)
    print(OUT)
    print(OUT.with_suffix(".pdf"))


if __name__ == "__main__":
    main()
