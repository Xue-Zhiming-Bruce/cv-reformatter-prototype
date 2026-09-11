"""DeepSeek Chat Completions transport for the experimental A-pipeline."""

from __future__ import annotations

import base64
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Literal

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from pydantic import BaseModel, ValidationError


class PipelineCallError(RuntimeError):
    def __init__(self, kind: Literal["transport", "schema", "model"], message: str, attempts: int):
        super().__init__(message)
        self.kind = kind
        self.attempts = attempts


def _image_data(path: Path) -> str:
    return f"data:image/png;base64,{base64.b64encode(path.read_bytes()).decode()}"


def _chat_content(text: str, image_paths: list[Path]) -> list[dict[str, Any]]:
    return [{"type": "text", "text": text}, *(
        {"type": "image_url", "image_url": {"url": _image_data(path)}} for path in image_paths
    )]


def _deepseek_client() -> OpenAI:
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
    if not api_key:
        raise RuntimeError("DeepSeek requires DEEPSEEK_API_KEY (or OPENAI_API_KEY fallback)")
    return OpenAI(api_key=api_key, base_url=base_url, timeout=180)


def visual_client() -> tuple[OpenAI, str] | None:
    """Owner decision 2026-09-08: the visual reviewer may run on a different
    OpenAI-compatible provider (e.g. Doubao via Volcano Ark or Qwen via
    DashScope) because the DeepSeek vision model's per-image compression is
    insufficient for fine-grained layout review. Configure:
        VISUAL_API_BASE  e.g. https://ark.cn-beijing.volces.com/api/v3
        VISUAL_API_KEY   provider key
        A_PIPELINE_VISUAL_MODEL  the provider's model id
    Unset -> the DeepSeek vision model is used, as before."""
    base = os.environ.get("VISUAL_API_BASE")
    key = os.environ.get("VISUAL_API_KEY")
    model = os.environ.get("A_PIPELINE_VISUAL_MODEL")
    if not (base and key and model):
        return None
    return OpenAI(api_key=key, base_url=base, timeout=600), model


def _usage_from(response: Any) -> dict[str, int]:
    return {
        "input_tokens": response.usage.prompt_tokens,
        "output_tokens": response.usage.completion_tokens,
        "total_tokens": response.usage.total_tokens,
    }


def _chat_completion(*, max_attempts: int = 2, client: OpenAI | None = None, stream: bool = False, **kwargs: Any) -> tuple[Any, int]:
    """Retry transient transport failures within the caller's budget.
    stream=True accumulates chunks — long generations (visual reviewer) exceed
    provider non-streaming gateway windows and get their connections dropped."""
    for attempt in range(1, max_attempts + 1):
        try:
            if stream:
                from types import SimpleNamespace
                chunks = (client or _deepseek_client()).chat.completions.create(
                    **kwargs,
                    stream=True,
                    stream_options={"include_usage": True},
                    extra_body={"thinking": {"type": "disabled"}},
                )
                content_parts: list[str] = []
                finish_reason = None
                usage = None
                for chunk in chunks:
                    if getattr(chunk, "usage", None):
                        usage = chunk.usage
                    if chunk.choices:
                        choice = chunk.choices[0]
                        if choice.delta and choice.delta.content:
                            content_parts.append(choice.delta.content)
                        if choice.finish_reason:
                            finish_reason = choice.finish_reason
                message = SimpleNamespace(content="".join(content_parts))
                response = SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish_reason)], usage=usage)
            else:
                response = (client or _deepseek_client()).chat.completions.create(
                    **kwargs,
                    extra_body={"thinking": {"type": "disabled"}},
                )
            choice = response.choices[0] if response.choices else None
            if choice is None or choice.message.content is None or choice.finish_reason not in {"stop", None}:
                raise PipelineCallError("model", "DeepSeek returned no complete content", attempt)
            return response, attempt
        except APITimeoutError as error:
            if attempt == max_attempts:
                raise PipelineCallError("transport", f"DeepSeek timed out after {attempt} transport attempt(s)", attempt) from error
        except APIConnectionError as error:
            if attempt == max_attempts:
                raise PipelineCallError("transport", f"DeepSeek connection failed after {attempt} transport attempt(s)", attempt) from error
        except APIStatusError as error:
            raise PipelineCallError("model", f"DeepSeek rejected the request: HTTP {error.status_code}", attempt) from error
    raise AssertionError("unreachable")


def _structured_chat_call(
    model: str,
    messages: list[dict[str, Any]],
    schema: type[BaseModel],
    *,
    allow_correction: bool = True,
    max_attempts: int | None = None,
    validate: Callable[[BaseModel], None] | None = None,
    client: OpenAI | None = None,
    stream: bool = False,
) -> tuple[BaseModel, dict[str, int]]:
    """Use JSON mode for syntax and local validation for the actual contract."""
    total_usage: Counter[str] = Counter()
    attempts = 0
    attempt_limit = max_attempts or 2
    request_messages = messages
    while attempts < attempt_limit:
        response, used = _chat_completion(
            max_attempts=min(2, attempt_limit - attempts),
            client=client,
            stream=stream,
            model=model,
            messages=request_messages,
            response_format={"type": "json_object"},
        )
        attempts += used
        total_usage.update(_usage_from(response))
        raw = response.choices[0].message.content
        try:
            result = schema.model_validate_json(raw)
            if validate:
                validate(result)
            return result, {**dict(total_usage), "attempts": attempts}
        except (ValidationError, ValueError) as error:
            if not allow_correction or attempts >= attempt_limit:
                raise PipelineCallError("schema", f"DeepSeek schema validation failed after {attempts} attempt(s): {error}", attempts) from error
            request_messages = [
                *messages,
                {"role": "assistant", "content": raw},
                {"role": "user", "content": json.dumps({
                    "instruction": "Re-evaluate the original context and images, then correct the JSON. Current validation facts are authoritative. Return JSON only.",
                    "schema": schema.model_json_schema(),
                    "validation_error": str(error),
                }, ensure_ascii=False, sort_keys=True)},
            ]
    raise AssertionError("unreachable")


def _html_call(prompt: str, model: str, image_paths: list[Path] | None = None) -> tuple[str, dict[str, int]]:
    content: str | list[dict[str, Any]] = prompt
    if image_paths:
        content = _chat_content(prompt, image_paths)
    response, attempts = _chat_completion(model=model, messages=[
        {"role": "system", "content": "Return only a complete HTML document. No Markdown fences or commentary."},
        {"role": "user", "content": content},
    ])
    document = re.sub(
        r"^```(?:html)?\s*|\s*```$", "", response.choices[0].message.content.strip(), flags=re.I | re.S
    ).strip()
    if not re.search(r"<!doctype html|<html\b", document, re.I):
        raise PipelineCallError("model", "DeepSeek did not return a complete HTML document", attempts)
    return document, {**_usage_from(response), "attempts": attempts}
