"""DeepSeek OpenAI-compatible summary model adapter."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(slots=True)
class DeepSeekSummaryClient:
    """Minimal DeepSeek client for one non-streaming summary completion."""

    model: str
    api_key: str
    base_url: str = "https://api.deepseek.com"
    temperature: float = 0.0
    thinking: bool = False

    @classmethod
    def from_environment(cls) -> "DeepSeekSummaryClient":
        """Build the client from explicit DeepSeek environment settings."""
        api_key = os.getenv("DEEPSEEK_API_KEY", "")
        model = os.getenv("DEEPSEEK_SUMMARY_MODEL", "")
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY is not set")
        if not model:
            raise ValueError(
                "DEEPSEEK_SUMMARY_MODEL is not set; choose a DeepSeek model"
            )

        thinking = os.getenv("DEEPSEEK_SUMMARY_THINKING", "").lower() in {
            "1",
            "true",
            "yes",
        }
        return cls(
            model=model,
            api_key=api_key,
            base_url=os.getenv(
                "DEEPSEEK_BASE_URL",
                "https://api.deepseek.com",
            ),
            thinking=thinking,
        )

    def complete(self, prompt: str, max_tokens: int) -> str:
        """Request one Markdown summary from DeepSeek."""
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")

        from openai import OpenAI

        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        request: dict[str, object] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if self.thinking:
            request["extra_body"] = {"thinking": {"type": "enabled"}}

        response = client.chat.completions.create(**request)
        content = response.choices[0].message.content or ""
        if not content:
            raise ValueError("DeepSeek returned empty summary content")
        return content
