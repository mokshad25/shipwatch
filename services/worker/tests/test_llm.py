"""make_chat_model picks the LangChain chat model for LLM_PROVIDER, with SDK retries off."""

import pytest
from agent.llm import LLMConfigError, make_chat_model
from langchain_anthropic import ChatAnthropic
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from worker.settings import WorkerSettings


def _settings(provider: str, **kw) -> WorkerSettings:
    return WorkerSettings(
        _env_file=None, llm_provider=provider, llm_model="some-model", llm_timeout_seconds=12, **kw
    )


@pytest.mark.parametrize(
    ("provider", "key_field", "cls"),
    [
        ("openai", "openai_api_key", ChatOpenAI),
        ("anthropic", "anthropic_api_key", ChatAnthropic),
        ("groq", "groq_api_key", ChatGroq),
    ],
)
def test_provider_picks_the_chat_model(provider, key_field, cls):
    llm = make_chat_model(_settings(provider, **{key_field: SecretStr("k-test")}))

    assert isinstance(llm, cls)
    assert llm.max_retries == 0  # retries go through the worker's budget
    assert getattr(llm, key_field).get_secret_value() == "k-test"


def test_groq_settings(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    llm = make_chat_model(_settings("groq", groq_api_key=SecretStr("k")))

    assert (llm.model_name, llm.request_timeout) == ("some-model", 12)


def test_model_is_required():
    with pytest.raises(LLMConfigError):
        make_chat_model(WorkerSettings(_env_file=None, llm_provider="groq", llm_model=""))
