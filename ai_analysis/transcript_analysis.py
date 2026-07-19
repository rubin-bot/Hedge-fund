from ai_analysis.gemini_client import GeminiClient

TRANSCRIPT_SYSTEM_PROMPT = """You are an equity research analyst. Read the earnings call transcript
excerpt provided and identify: (1) changes in guidance, (2) shifts in management tone or confidence,
(3) notable analyst questions and how directly they were answered. Be concise and specific, citing
the relevant text."""


def analyze_transcript(client: GeminiClient, ticker: str, transcript_text: str) -> str:
    user_prompt = f"Ticker: {ticker}\n\nTranscript excerpt:\n{transcript_text}"
    return client.complete(system=TRANSCRIPT_SYSTEM_PROMPT, user=user_prompt)
