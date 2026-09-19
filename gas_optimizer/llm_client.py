"""
Hugging Face Inference client for optimization proposals.

Hardened in three ways the previous version was not:

* the token is read lazily, so importing this module never requires one;
* transient API failures are retried with backoff instead of collapsing the
  whole attempt into `None`;
* `provider` is only forwarded when the installed huggingface_hub accepts it,
  since routing moved behind that parameter in recent releases.
"""

from __future__ import annotations

import inspect
import sys
import time
from functools import lru_cache
from typing import Any

from huggingface_hub import InferenceClient

from . import config, prompts, utils

# Whole contracts come back, not diffs, so the ceiling has to fit a full file.
MAX_TOKENS = 4096
TEMPERATURE = 0.1

_TRANSIENT_HINTS = (
    "429",
    "500",
    "502",
    "503",
    "504",
    "timeout",
    "timed out",
    "overloaded",
    "rate limit",
    "currently loading",
    "temporarily unavailable",
)


class LLMError(RuntimeError):
    """The model could not be reached, or returned nothing usable."""


@lru_cache(maxsize=1)
def _client() -> InferenceClient:
    kwargs: dict[str, Any] = {"api_key": config.get_hf_token()}

    provider = config.HF_PROVIDER
    if provider and "provider" in inspect.signature(InferenceClient.__init__).parameters:
        kwargs["provider"] = provider

    return InferenceClient(**kwargs)


def _is_transient(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(hint in text for hint in _TRANSIENT_HINTS)


def _complete(system: str, user: str, model: str | None = None) -> str:
    model_id = model or config.MODEL_ID
    last: Exception | None = None

    for attempt in range(1, config.API_RETRIES + 1):
        try:
            response = _client().chat.completions.create(
                model=model_id,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=TEMPERATURE,
                max_tokens=MAX_TOKENS,
            )
        except Exception as exc:  # huggingface_hub raises a wide range of types
            last = exc
            if attempt == config.API_RETRIES or not _is_transient(exc):
                break
            delay = 2 ** (attempt - 1)
            # stderr: stdout may be carrying a machine-readable event stream.
            print(
                f"    [API] {type(exc).__name__}: {exc} — retrying in {delay}s",
                file=sys.stderr,
            )
            time.sleep(delay)
            continue

        content = response.choices[0].message.content if response.choices else None
        if content:
            return content
        last = LLMError("the model returned an empty message")

    raise LLMError(f"{model_id} could not be reached: {last}") from last


def get_optimization_proposal(
    current_code: str, retry_info: dict | None = None, *, model: str | None = None
) -> str:
    """Ask for an optimized version of `current_code`.

    Returns the cleaned contract source. Raises LLMError if the API could not
    be reached or produced nothing usable.
    """
    if retry_info is None:
        user = prompts.USER_PROMPT_FIRST.format(code=current_code)
    else:
        user = prompts.USER_PROMPT_RETRY.format(
            compile=retry_info.get("compile", "true"),
            test=retry_info.get("test", "N/A"),
            type=retry_info.get("type", "unknown"),
            error=retry_info.get("error", "unspecified"),
            trace=retry_info.get("trace", "N/A"),
            code=current_code,
        )

    candidate = utils.clean_llm_response(_complete(prompts.SYSTEM_PROMPT, user, model=model))
    if not candidate:
        raise LLMError("the model returned an empty contract")
    return candidate
