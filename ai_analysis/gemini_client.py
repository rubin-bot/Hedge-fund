import time

from google import genai
from google.genai import types
from google.genai.errors import ClientError

from config.settings import settings

# Alias that always resolves to Google's current recommended flash model,
# rather than a pinned version that can get deprecated for new API keys.
DEFAULT_MODEL = "gemini-flash-latest"
MAX_RETRIES = 5
INITIAL_BACKOFF_SECONDS = 2.0


class GeminiClient:
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL):
        self.client = genai.Client(api_key=api_key or settings.google_api_key)
        self.model = model

    def complete(self, system: str, user: str, max_output_tokens: int = 2048) -> str:
        # Free-tier rate limits (RPM/TPM/RPD) are hit often on batch runs — retry with
        # exponential backoff on 429s rather than failing the whole ingestion job.
        backoff = INITIAL_BACKOFF_SECONDS
        for attempt in range(MAX_RETRIES):
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=user,
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        max_output_tokens=max_output_tokens,
                    ),
                )
                return response.text
            except ClientError as exc:
                if exc.code != 429 or attempt == MAX_RETRIES - 1:
                    raise
                time.sleep(backoff)
                backoff *= 2
        raise RuntimeError("unreachable")
