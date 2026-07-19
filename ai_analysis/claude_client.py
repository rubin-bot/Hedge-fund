import anthropic

from config.settings import settings

DEFAULT_MODEL = "claude-sonnet-5"


class ClaudeClient:
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL):
        self.client = anthropic.Anthropic(api_key=api_key or settings.anthropic_api_key)
        self.model = model

    def complete(self, system: str, user: str, max_tokens: int = 2048) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text
