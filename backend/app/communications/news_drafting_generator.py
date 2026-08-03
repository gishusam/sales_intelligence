"""AI draft-generator boundary with a fail-closed Gemini adapter."""

import json
import os
from typing import Any, Protocol
from urllib import error, request


class NewsDraftGeneratorError(RuntimeError):
    pass


class NewsDraftGeneratorConfigurationError(
    NewsDraftGeneratorError
):
    pass


class NewsDraftGenerator(Protocol):
    model_name: str

    def generate(
        self,
        generation_request: dict[str, Any],
    ) -> dict[str, Any]:
        ...


class GeminiNewsDraftGenerator:
    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        timeout_seconds: int = 45,
    ):
        self.api_key = api_key
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds

    def generate(
        self,
        generation_request: dict[str, Any],
    ) -> dict[str, Any]:
        prompt = generation_request["prompt"]
        endpoint = (
            "https://generativelanguage.googleapis.com/v1beta/"
            f"models/{self.model_name}:generateContent"
            f"?key={self.api_key}"
        )
        body = json.dumps(
            {
                "contents": [
                    {
                        "parts": [
                            {
                                "text": prompt,
                            }
                        ]
                    }
                ],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "temperature": 0.3,
                },
            }
        ).encode("utf-8")
        http_request = request.Request(
            endpoint,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        try:
            with request.urlopen(
                http_request,
                timeout=self.timeout_seconds,
            ) as response:
                payload = json.loads(
                    response.read().decode("utf-8")
                )
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise NewsDraftGeneratorError(
                "AI newsletter draft generation failed"
            ) from exc

        try:
            text_value = payload["candidates"][0]["content"]["parts"][0]["text"]
            generated = json.loads(text_value)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise NewsDraftGeneratorError(
                "AI generator returned an invalid response"
            ) from exc

        return generated


def build_default_generator() -> NewsDraftGenerator:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()

    if not api_key:
        raise NewsDraftGeneratorConfigurationError(
            "GEMINI_API_KEY is required for AI newsletter drafting"
        )

    return GeminiNewsDraftGenerator(
        api_key=api_key,
        model_name=os.getenv(
            "NEWSLETTER_AI_MODEL",
            "gemini-2.5-flash",
        ),
        timeout_seconds=int(
            os.getenv(
                "NEWSLETTER_AI_TIMEOUT_SECONDS",
                "45",
            )
        ),
    )
