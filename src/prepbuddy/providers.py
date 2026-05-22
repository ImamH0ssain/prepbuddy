"""LLM provider adapters for Gemini, Ollama, and deterministic tests."""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Protocol

import httpx

from .schemas import AnswerChoice, GeneratedQuestionSet, GenerationRequest, MCQ, ProviderResult
from .settings import Settings


class ProviderError(RuntimeError):
    """Raised when an LLM provider cannot generate valid MCQs."""


class LLMProvider(Protocol):
    """Protocol implemented by all MCQ generation providers."""

    name: str
    model: str

    def generate_mcqs(self, request: GenerationRequest) -> GeneratedQuestionSet:
        """Generate structured MCQs for the requested sections."""


class FakeProvider:
    """Deterministic provider used by tests, demos, and offline smoke runs."""

    name = "fake"
    model = "deterministic-fake"

    def generate_mcqs(self, request: GenerationRequest) -> GeneratedQuestionSet:
        """Generate predictable MCQs while honoring weak-topic context."""
        questions: list[MCQ] = []
        weak_by_section: dict[int, list[str]] = {}
        for item in request.adaptation_context.weak_topics:
            section_id = int(item.get("section_id", 0))
            weak_by_section.setdefault(section_id, []).append(str(item.get("topic", "")))

        for section in request.sections:
            weak_topics = weak_by_section.get(section.canonical_id, [])
            for number in range(1, request.questions_per_section + 1):
                if weak_topics and number <= len(weak_topics):
                    topic = weak_topics[number - 1]
                else:
                    topic = f"section-{section.canonical_id}-topic-{number}"
                question_text = (
                    f"For {section.title}, which statement best matches {topic}? "
                    f"(generated item {number})"
                )
                choices = [
                    AnswerChoice(label="A", text=f"The dossier supports {topic}."),
                    AnswerChoice(label="B", text=f"{topic} is unrelated to this section."),
                    AnswerChoice(label="C", text=f"{topic} contradicts the section title."),
                    AnswerChoice(label="D", text=f"The document omits {topic}."),
                ]
                questions.append(
                    MCQ(
                        section_id=section.canonical_id,
                        topic=topic,
                        question=question_text,
                        choices=choices,
                        correct_answer="A",
                        explanation=f"The relevant section context supports {topic}.",
                        source_chunk_ids=section.chunk_ids[:1],
                    )
                )
        return GeneratedQuestionSet(
            questions=questions,
            provider_result=ProviderResult(provider=self.name, model=self.model),
        )


class OllamaProvider:
    """Local Ollama provider using schema-constrained JSON output."""

    name = "ollama"

    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.model = settings.ollama_model
        self.timeout = settings.ollama_timeout_seconds

    def available(self) -> bool:
        """Return whether the configured Ollama server responds."""
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=2)
            return response.status_code == 200 and self.model in response.text
        except httpx.HTTPError:
            return False

    def generate_mcqs(self, request: GenerationRequest) -> GeneratedQuestionSet:
        """Generate MCQs through Ollama's chat API."""
        started = time.perf_counter()
        payload = {
            "model": self.model,
            "stream": False,
            "format": _question_set_schema(),
            "messages": [
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": _user_prompt(request)},
            ],
            "options": {
                "temperature": 0.1,
                "top_p": 0.8,
                "num_ctx": 8192,
                "num_predict": _max_output_tokens(request),
            },
        }
        try:
            response = httpx.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
            response.raise_for_status()
            content = response.json()["message"]["content"]
            questions = _parse_questions_payload(content)
        except Exception as exc:
            raise ProviderError(f"Ollama generation failed: {exc}") from exc
        return GeneratedQuestionSet(
            questions=questions,
            provider_result=ProviderResult(
                provider=self.name,
                model=self.model,
                latency_ms=int((time.perf_counter() - started) * 1000),
            ),
        )


class GeminiProvider:
    """Gemini provider using structured JSON responses when an API key is configured."""

    name = "gemini"

    def __init__(self, settings: Settings) -> None:
        self.api_key = settings.gemini_api_key or os.getenv("GEMINI_API_KEY")
        self.model = settings.gemini_model
        if not self.api_key:
            raise ProviderError("GEMINI_API_KEY is not set")

    def generate_mcqs(self, request: GenerationRequest) -> GeneratedQuestionSet:
        """Generate MCQs using the Google GenAI SDK."""
        started = time.perf_counter()
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=self.api_key)
            response = client.models.generate_content(
                model=self.model,
                contents=_user_prompt(request),
                config=types.GenerateContentConfig(
                    system_instruction=_system_prompt(),
                    response_mime_type="application/json",
                    response_schema=_question_set_schema(),
                    temperature=0.2,
                    max_output_tokens=_max_output_tokens(request),
                ),
            )
            questions = _parse_questions_payload(response.text or "{}")
        except Exception as exc:
            raise ProviderError(f"Gemini generation failed: {exc}") from exc
        return GeneratedQuestionSet(
            questions=questions,
            provider_result=ProviderResult(
                provider=self.name,
                model=self.model,
                latency_ms=int((time.perf_counter() - started) * 1000),
            ),
        )


def make_provider(settings: Settings, provider_name: str | None = None) -> LLMProvider:
    """Create the requested provider, applying the project's auto fallback order."""
    selected = provider_name or settings.llm_provider
    if selected == "fake":
        return FakeProvider()
    if selected == "gemini":
        return GeminiProvider(settings)
    if selected == "ollama":
        provider = OllamaProvider(settings)
        if not provider.available():
            raise ProviderError(
                f"Ollama model '{settings.ollama_model}' is not reachable at {settings.ollama_base_url}"
            )
        return provider
    if settings.gemini_api_key or os.getenv("GEMINI_API_KEY"):
        return GeminiProvider(settings)
    provider = OllamaProvider(settings)
    if provider.available():
        return provider
    raise ProviderError("No LLM provider is available. Set GEMINI_API_KEY or start Ollama with qwen3:4b-instruct.")


def question_fingerprint(question: MCQ) -> str:
    """Create a stable fingerprint for duplicate/repetition checks."""
    payload = {
        "section_id": question.section_id,
        "topic": question.topic.strip().lower(),
        "question": question.question.strip().lower(),
        "choices": [choice.text.strip().lower() for choice in question.choices],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _parse_questions_payload(content: str) -> list[MCQ]:
    payload = json.loads(content)
    raw_questions = payload.get("questions", payload if isinstance(payload, list) else [])
    if not isinstance(raw_questions, list):
        raise ProviderError("Provider response did not contain a questions list")
    return [MCQ.model_validate(item) for item in raw_questions]


def _system_prompt() -> str:
    return (
        "You generate assessment-quality multiple-choice questions from supplied document sections. "
        "Return only valid JSON. Each question must have exactly four choices A-D, one correct answer, "
        "a concise explanation, a topic tag, and references to supplied section IDs."
    )


def _user_prompt(request: GenerationRequest) -> str:
    total = len(request.sections) * request.questions_per_section
    weak_topics = request.adaptation_context.weak_topics[:12]
    avoid_fingerprints = request.adaptation_context.avoid_fingerprints[:24]
    section_blocks = []
    for section in request.sections:
        section_blocks.append(
            "\n".join(
                [
                    f"Section {section.canonical_id}: {section.title}",
                    f"Source label: {section.source_label}",
                    f"Chunk IDs: {', '.join(section.chunk_ids)}",
                    section.text[:2600],
                ]
            )
        )
    return (
        f"Return JSON only. Generate exactly {total} questions total: "
        f"exactly {request.questions_per_section} questions for each section ID provided.\n"
        "Each question must be answerable from the supplied excerpts and must include section_id, topic, "
        "question, four choices A-D, correct_answer, explanation, and source_chunk_ids.\n"
        f"Prior completed sessions for these sections: {request.adaptation_context.prior_session_count}.\n"
        f"Weak topics to prioritize: {json.dumps(weak_topics, ensure_ascii=True)}\n"
        f"Recent question fingerprints to avoid: {json.dumps(avoid_fingerprints, ensure_ascii=True)}\n\n"
        f"Sections:\n\n{chr(10).join(section_blocks)}\n\n"
        "Do not stop early. If context is limited, still produce the exact requested count from the supplied excerpts."
    )


def _max_output_tokens(request: GenerationRequest) -> int:
    """Estimate enough JSON output budget for the requested number of MCQs."""
    total = max(1, len(request.sections) * request.questions_per_section)
    return min(16000, max(2048, 500 + total * 650))


def _question_set_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "section_id": {"type": "integer"},
                        "topic": {"type": "string"},
                        "question": {"type": "string"},
                        "choices": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string", "enum": ["A", "B", "C", "D"]},
                                    "text": {"type": "string"},
                                },
                                "required": ["label", "text"],
                            },
                            "minItems": 4,
                            "maxItems": 4,
                        },
                        "correct_answer": {"type": "string", "enum": ["A", "B", "C", "D"]},
                        "explanation": {"type": "string"},
                        "source_chunk_ids": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": [
                        "section_id",
                        "topic",
                        "question",
                        "choices",
                        "correct_answer",
                        "explanation",
                        "source_chunk_ids",
                    ],
                },
            }
        },
        "required": ["questions"],
    }
