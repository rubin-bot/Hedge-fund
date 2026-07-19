from ai_analysis.claude_client import ClaudeClient

FILING_SYSTEM_PROMPT = """You are an equity research analyst. Read the filing excerpt provided
and identify: (1) material risk factors, (2) red flags or unusual disclosures, (3) overall tone
relative to prior filings if context is given. Be concise and specific, citing the relevant text."""


def analyze_filing(client: ClaudeClient, ticker: str, filing_text: str) -> str:
    user_prompt = f"Ticker: {ticker}\n\nFiling excerpt:\n{filing_text}"
    return client.complete(system=FILING_SYSTEM_PROMPT, user=user_prompt)
