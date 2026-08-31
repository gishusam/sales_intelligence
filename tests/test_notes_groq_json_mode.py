import asyncio

from app.routers import notes


class FakeResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"score":"NURTURE",'
                            '"reason":"Follow up later",'
                            '"follow_up_days":30,'
                            '"signals":["interested"]}'
                        )
                    }
                }
            ]
        }


class FakeAsyncClient:
    last_payload = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def post(self, url, headers=None, json=None):
        FakeAsyncClient.last_payload = json
        return FakeResponse()


def test_groq_scoring_requests_valid_json_mode(monkeypatch):
    monkeypatch.setattr(notes, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(notes.httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        notes.score_with_llm(
            {
                "name": "Example Lead",
                "lead_type": "landlord",
                "area": "Nairobi",
                "score": 50,
                "phone": "0700000000",
                "website": None,
            },
            "Interested, but call again next month",
        )
    )

    payload = FakeAsyncClient.last_payload

    assert payload["response_format"] == {"type": "json_object"}
    assert payload["reasoning_effort"] == "low"
    assert payload["include_reasoning"] is False
    assert payload["max_completion_tokens"] >= 1024
    assert result["score"] == "NURTURE"
