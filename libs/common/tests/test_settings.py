import pytest
from pydantic import ValidationError
from shipwatch_common.settings import Settings


@pytest.mark.parametrize("secret", ["", "   ", "change-me", "short-secret"])
def test_weak_webhook_secret_is_rejected(secret):
    with pytest.raises(ValidationError, match="GITHUB_WEBHOOK_SECRET"):
        Settings(github_webhook_secret=secret, _env_file=None)


def test_strong_webhook_secret_is_accepted():
    settings = Settings(github_webhook_secret="x" * 32, _env_file=None)

    assert settings.github_webhook_secret.get_secret_value() == "x" * 32
