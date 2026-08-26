"""Tests for the real LLMClient implementations wrapping the Groq and
Anthropic SDKs.

No real network calls or API keys — a fake object shaped like the SDK's
response type is injected in place of the real `groq.Groq()` /
`anthropic.Anthropic()` client, so these tests verify our extraction logic
(text + token usage) without touching the network.
"""

from dataclasses import dataclass, field

from app.core.matcher.llm_clients import AnthropicLLMClient, GroqLLMClient


@dataclass
class FakeUsage:
    prompt_tokens: int
    completion_tokens: int


@dataclass
class FakeMessage:
    content: str


@dataclass
class FakeChoice:
    message: FakeMessage


@dataclass
class FakeChatCompletion:
    choices: list[FakeChoice]
    usage: FakeUsage


@dataclass
class FakeCompletions:
    response: FakeChatCompletion
    calls: list[dict] = field(default_factory=list)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


@dataclass
class FakeChat:
    completions: FakeCompletions


@dataclass
class FakeGroqClient:
    chat: FakeChat


def test_groq_llm_client_extracts_text_and_token_usage():
    response = FakeChatCompletion(
        choices=[FakeChoice(message=FakeMessage(content='{"decision": "MATCH"}'))],
        usage=FakeUsage(prompt_tokens=120, completion_tokens=30),
    )
    completions = FakeCompletions(response=response)
    fake_client = FakeGroqClient(chat=FakeChat(completions=completions))
    client = GroqLLMClient(model="llama-3.3-70b-versatile", client=fake_client)

    result = client.complete("some prompt")

    assert result.text == '{"decision": "MATCH"}'
    assert result.input_tokens == 120
    assert result.output_tokens == 30


def test_groq_llm_client_sends_model_and_prompt():
    response = FakeChatCompletion(
        choices=[FakeChoice(message=FakeMessage(content="{}"))],
        usage=FakeUsage(prompt_tokens=1, completion_tokens=1),
    )
    completions = FakeCompletions(response=response)
    fake_client = FakeGroqClient(chat=FakeChat(completions=completions))
    client = GroqLLMClient(model="llama-3.3-70b-versatile", client=fake_client)

    client.complete("reconcile this record")

    call = completions.calls[0]
    assert call["model"] == "llama-3.3-70b-versatile"
    assert call["messages"] == [{"role": "user", "content": "reconcile this record"}]


@dataclass
class FakeAnthropicUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class FakeTextBlock:
    text: str
    type: str = "text"


@dataclass
class FakeAnthropicMessage:
    content: list[FakeTextBlock]
    usage: FakeAnthropicUsage


@dataclass
class FakeMessages:
    response: FakeAnthropicMessage
    calls: list[dict] = field(default_factory=list)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


@dataclass
class FakeAnthropicClient:
    messages: FakeMessages


def test_anthropic_llm_client_extracts_text_and_token_usage():
    response = FakeAnthropicMessage(
        content=[FakeTextBlock(text='{"decision": "MATCH"}')],
        usage=FakeAnthropicUsage(input_tokens=200, output_tokens=50),
    )
    fake_client = FakeAnthropicClient(messages=FakeMessages(response=response))
    client = AnthropicLLMClient(model="claude-sonnet-4-5", client=fake_client)

    result = client.complete("some prompt")

    assert result.text == '{"decision": "MATCH"}'
    assert result.input_tokens == 200
    assert result.output_tokens == 50


def test_anthropic_llm_client_concatenates_multiple_text_blocks():
    response = FakeAnthropicMessage(
        content=[FakeTextBlock(text="part one "), FakeTextBlock(text="part two")],
        usage=FakeAnthropicUsage(input_tokens=1, output_tokens=1),
    )
    fake_client = FakeAnthropicClient(messages=FakeMessages(response=response))
    client = AnthropicLLMClient(model="claude-sonnet-4-5", client=fake_client)

    result = client.complete("some prompt")

    assert result.text == "part one part two"


def test_anthropic_llm_client_sends_model_and_prompt():
    response = FakeAnthropicMessage(
        content=[FakeTextBlock(text="{}")],
        usage=FakeAnthropicUsage(input_tokens=1, output_tokens=1),
    )
    messages = FakeMessages(response=response)
    fake_client = FakeAnthropicClient(messages=messages)
    client = AnthropicLLMClient(model="claude-sonnet-4-5", client=fake_client)

    client.complete("reconcile this record")

    call = messages.calls[0]
    assert call["model"] == "claude-sonnet-4-5"
    assert call["messages"] == [{"role": "user", "content": "reconcile this record"}]
