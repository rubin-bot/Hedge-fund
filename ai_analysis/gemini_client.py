import time
from typing import Callable, TypeVar

from google import genai
from google.genai import types
from google.genai.errors import ClientError
from pydantic import BaseModel

from ai_analysis.rate_limiter import RateLimiter
from config.settings import load_gemini_config, settings

# Alias that always resolves to Google's current recommended flash model,
# rather than a pinned version that can get deprecated for new API keys.
DEFAULT_MODEL = "gemini-flash-latest"
MAX_RETRIES = 5
INITIAL_BACKOFF_SECONDS = 2.0
DEFAULT_MAX_OUTPUT_TOKENS = 2048

# gemini-flash-latest free tier, confirmed against Google's docs/AI Studio
# guidance as of 2026-07: ~15 requests/minute, ~1500 requests/day. These
# drift over time, so they're overridable via config.yaml's `gemini:` block
# rather than hardcoded elsewhere.
DEFAULT_REQUESTS_PER_MINUTE = 15
DEFAULT_REQUESTS_PER_DAY = 1500

T = TypeVar("T")


class GeminiAnalysisError(RuntimeError):
    """Raised when Gemini's response doesn't conform to the requested
    structured schema (response.parsed is None) — this means the model
    produced something the caller can't use, so it should surface loudly
    rather than handing back None for a caller to mishandle.
    """


class GeminiClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        requests_per_minute: int | None = None,
        requests_per_day: int | None = None,
        max_output_tokens: int | None = None,
    ):
        self.client = genai.Client(api_key=api_key or settings.google_api_key)

        # explicit arg > config.yaml `gemini:` block > hardcoded default
        config = load_gemini_config()
        self.model = model or config.get("model", DEFAULT_MODEL)
        self.default_max_output_tokens = max_output_tokens or config.get(
            "max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS
        )
        self._rate_limiter = RateLimiter(
            requests_per_minute=requests_per_minute or config.get("requests_per_minute", DEFAULT_REQUESTS_PER_MINUTE),
            requests_per_day=requests_per_day or config.get("requests_per_day", DEFAULT_REQUESTS_PER_DAY),
        )

    def _call_with_retry(self, make_request: Callable[[], T]) -> T:
        # Free-tier rate limits (RPM/TPM/RPD) are hit often on batch runs even with
        # proactive throttling (bursts, other processes sharing the same key) — retry
        # with exponential backoff on 429s rather than failing the whole ingestion job.
        backoff = INITIAL_BACKOFF_SECONDS
        for attempt in range(MAX_RETRIES):
            self._rate_limiter.acquire()
            try:
                return make_request()
            except ClientError as exc:
                if exc.code != 429 or attempt == MAX_RETRIES - 1:
                    raise
                time.sleep(backoff)
                backoff *= 2
        raise RuntimeError("unreachable")

    def complete(self, system: str, user: str, max_output_tokens: int | None = None) -> str:
        def make_request() -> str:
            response = self.client.models.generate_content(
                model=self.model,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    max_output_tokens=max_output_tokens or self.default_max_output_tokens,
                ),
            )
            return response.text

        return self._call_with_retry(make_request)

    def complete_structured(
        self,
        system: str,
        user: str,
        response_schema: type[BaseModel],
        max_output_tokens: int | None = None,
    ) -> BaseModel:
        def make_request() -> BaseModel:
            response = self.client.models.generate_content(
                model=self.model,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    max_output_tokens=max_output_tokens or self.default_max_output_tokens,
                    response_mime_type="application/json",
                    response_schema=response_schema,
                ),
            )
            if response.parsed is None:
                raise GeminiAnalysisError(
                    f"Gemini response did not conform to {response_schema.__name__}: {response.text!r}"
                )
            return response.parsed

        return self._call_with_retry(make_request)
