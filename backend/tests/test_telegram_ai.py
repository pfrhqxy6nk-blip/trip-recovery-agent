from datetime import UTC, datetime

from app.models.enums import OnboardingStep
from app.models.telegram import TravelerProfile
from app.services.ai_connections import AiConnectionService
from app.services.memory import InMemoryIncidentRepository
from app.services.telegram_ai import TelegramAiConnectionService

from tests.test_ai_connections import AcceptCredential, MemorySecretStore


async def test_connect_view_uses_fragment_handoff_and_never_requests_chat_paste() -> None:
    now = datetime(2026, 8, 17, tzinfo=UTC)
    repository = InMemoryIncidentRepository()
    await repository.save_traveler(
        TravelerProfile(
            user_id="telegram:101",
            telegram_user_id="101",
            telegram_chat_id="202",
            onboarding_step=OnboardingStep.COMPLETE,
            active_policy_version=1,
            created_at=now,
            updated_at=now,
        )
    )
    connections = AiConnectionService(repository, MemorySecretStore(), AcceptCredential())
    telegram = TelegramAiConnectionService(
        repository, connections, "https://agent.example/connections/gemini"
    )

    view = await telegram.handle(
        telegram_user_id="101",
        telegram_chat_id="202",
        callback_data="ai:connect",
        now=now,
    )

    secure_url = view.button_rows[1][0].url
    assert secure_url is not None
    assert secure_url.startswith("https://agent.example/connections/gemini#")
    assert "?token=" not in secure_url
    assert "Never send it in Telegram" in view.text
    assert "aistudio.google.com" in (view.button_rows[0][0].url or "")
