"""
LLM client for communicating with the optimization model.
"""

from functools import lru_cache

from huggingface_hub import InferenceClient

from . import config
from . import prompts
from . import utils


@lru_cache(maxsize=1)
def _get_client() -> InferenceClient:
    """Build the client on first use so importing this module needs no token."""
    return InferenceClient(api_key=config.get_hf_token())


def get_optimization_proposal(current_code: str, retry_info: dict | None = None) -> str | None:
    """
    Sends code to the LLM for optimization.

    Args:
        current_code: The Solidity code to optimize
        retry_info: If None, sends first-try prompt. Otherwise, sends retry prompt with failure details.

    Returns:
        Cleaned diff patch string, or None on error
    """
    if retry_info is None:
        print(">> Sending FIRST TRY request to LLM...")
        user_content = prompts.USER_PROMPT_FIRST.format(code=current_code)
    else:
        attempt = retry_info.get("attempt", "?")
        print(f">> Sending RETRY request (Attempt #{attempt})...")
        user_content = prompts.USER_PROMPT_RETRY.format(
            compile=retry_info.get("compile", "true"),
            test=retry_info.get("test", "NONE"),
            type=retry_info.get("type", "unknown"),
            error=retry_info.get("error", "Check logic"),
            trace=retry_info.get("trace", "N/A"),
            code=current_code,
        )

    try:
        response = _get_client().chat.completions.create(
            model=config.MODEL_ID,
            messages=[
                {"role": "system", "content": prompts.SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.1,
            max_tokens=2000,
        )
        return utils.clean_llm_response(response.choices[0].message.content)
    except Exception as e:
        print(f"[API ERROR]: {e}")
        return None
