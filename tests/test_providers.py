from __future__ import annotations

from prepbuddy.providers import OllamaProvider, _user_prompt
from prepbuddy.schemas import AdaptationContext, GenerationRequest, GenerationSection
from prepbuddy.settings import Settings


def _request(questions_per_section: int = 5) -> GenerationRequest:
    return GenerationRequest(
        sections=[
            GenerationSection(
                canonical_id=1,
                source_label="Section 1",
                title="Alpha",
                text="Alpha context",
                chunk_ids=["doc1:s1:c1"],
            ),
            GenerationSection(
                canonical_id=2,
                source_label="Section 2",
                title="Beta",
                text="Beta context",
                chunk_ids=["doc1:s2:c1"],
            ),
        ],
        questions_per_section=questions_per_section,
        adaptation_context=AdaptationContext(
            weak_topics=[{"section_id": 1, "topic": "weak"}],
            avoid_fingerprints=["abc", "def"],
        ),
    )


def test_generation_prompt_requires_exact_total_and_per_section_counts() -> None:
    prompt = _user_prompt(_request(questions_per_section=5))

    assert "exactly 10 questions total" in prompt
    assert "exactly 5 questions for each section" in prompt
    assert "JSON only" in prompt


def test_ollama_num_predict_scales_with_requested_question_count(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"message": {"content": "{\"questions\": []}"}}

    def fake_post(url: str, json: dict[str, object], timeout: float) -> Response:
        captured["payload"] = json
        return Response()

    monkeypatch.setattr("prepbuddy.providers.httpx.post", fake_post)
    provider = OllamaProvider(Settings(ollama_model="qwen3:4b-instruct"))

    provider.generate_mcqs(_request(questions_per_section=20))

    payload = captured["payload"]
    assert isinstance(payload, dict)
    options = payload["options"]
    assert isinstance(options, dict)
    assert options["num_predict"] > 2048
