"""Real LLMClient implementations for Tier 3 (Groq) and Tier 4 (Anthropic),
wrapping the SDKs behind the same `LLMClient` protocol llm_adjudicator.py
adjudicates against — so `adjudicate()` never needs to know which provider
it's talking to.

Reads API keys from the environment via each SDK's own default behavior
(GROQ_API_KEY / ANTHROPIC_API_KEY). Not exercised by the test suite — see
CLAUDE.md: "Mock LLM clients in unit tests; don't burn API calls in CI." A
small number of real-call integration tests are fine to run manually before
a demo, not on every save.
"""

from __future__ import annotations

import anthropic
import groq

from app.core.matcher.llm_adjudicator import LLMResponse


class GroqLLMClient:
    def __init__(self, model: str, client: groq.Groq | None = None) -> None:
        self.model = model
        self._client = client or groq.Groq()

    def complete(self, prompt: str) -> LLMResponse:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        return LLMResponse(
            text=response.choices[0].message.content,
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
        )


class AnthropicLLMClient:
    def __init__(
        self,
        model: str,
        client: anthropic.Anthropic | None = None,
        max_tokens: int = 1024,
    ) -> None:
        self.model = model
        self._client = client or anthropic.Anthropic()
        self._max_tokens = max_tokens

    def complete(self, prompt: str) -> LLMResponse:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        return LLMResponse(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
