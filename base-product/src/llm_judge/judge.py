"""Core comparison logic for llm-judge."""
import os
import time
from dataclasses import dataclass

import httpx


MODEL_ALIASES = {
    "claude-haiku": "claude-haiku-4-5-20251001",
    "claude-sonnet": "claude-sonnet-4-20250514",
    "claude-opus": "claude-opus-4-20250514",
}


@dataclass
class ModelResult:
    model: str
    response: str
    latency_ms: float
    input_tokens: int
    output_tokens: int
    error: str | None = None


def _resolve_model(alias: str) -> str:
    return MODEL_ALIASES.get(alias, alias)


def _call_anthropic(prompt: str, model: str, max_tokens: int, temperature: float) -> ModelResult:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return ModelResult(
            model=model, response="", latency_ms=0,
            input_tokens=0, output_tokens=0, error="ANTHROPIC_API_KEY not set",
        )

    resolved = _resolve_model(model)
    start = time.perf_counter()

    try:
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": resolved,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60.0,
        )
        elapsed = (time.perf_counter() - start) * 1000
        data = resp.json()

        if resp.status_code != 200:
            return ModelResult(
                model=model, response="", latency_ms=elapsed,
                input_tokens=0, output_tokens=0,
                error=data.get("error", {}).get("message", f"HTTP {resp.status_code}"),
            )

        text = "".join(
            block["text"] for block in data.get("content", []) if block.get("type") == "text"
        )
        usage = data.get("usage", {})

        return ModelResult(
            model=model,
            response=text,
            latency_ms=elapsed,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )
    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000
        return ModelResult(
            model=model, response="", latency_ms=elapsed,
            input_tokens=0, output_tokens=0, error=str(e),
        )


def compare_models(
    prompt: str,
    models: list[str],
    max_tokens: int = 256,
    temperature: float = 0.7,
) -> list[ModelResult]:
    results = []
    for model in models:
        result = _call_anthropic(prompt, model, max_tokens, temperature)
        results.append(result)
    return results
