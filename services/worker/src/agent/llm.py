"""Build the chat model named by LLM_PROVIDER / LLM_MODEL (SPEC_W3 D2)."""

from langchain_core.language_models import BaseChatModel
from worker.settings import WorkerSettings


class LLMConfigError(ValueError):
    pass


def make_chat_model(settings: WorkerSettings) -> BaseChatModel:
    """SDK retries default to 0 (LLM_MAX_RETRIES): the worker's own budget does the retrying."""
    if not settings.llm_model:
        raise LLMConfigError("set LLM_MODEL (and LLM_PROVIDER) to run the agent")
    if settings.llm_provider == "openai":
        from langchain_openai import ChatOpenAI

        # Without an explicit key the client falls back to OPENAI_API_KEY from the environment.
        key = {"api_key": settings.openai_api_key} if settings.openai_api_key else {}
        return ChatOpenAI(
            model=settings.llm_model,
            **key,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )
    if settings.llm_provider == "groq":
        from langchain_groq import ChatGroq

        key = {"api_key": settings.groq_api_key} if settings.groq_api_key else {}
        return ChatGroq(
            model=settings.llm_model,
            **key,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )
    from langchain_anthropic import ChatAnthropic

    # No temperature and no thinking config: current Claude models reject or ignore both.
    key = {"api_key": settings.anthropic_api_key} if settings.anthropic_api_key else {}
    return ChatAnthropic(
        model=settings.llm_model,
        **key,
        default_request_timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        max_tokens=16_000,
    )
