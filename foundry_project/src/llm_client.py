"""
LLM client for communicating with the optimization model.
"""

from huggingface_hub import InferenceClient

import config
import prompts
import utils


# Initialize client
client = InferenceClient(api_key=config.HF_TOKEN)


def get_optimization_proposal(current_code: str, retry_info: dict | None = None) -> str | None:
    """
    Sends code to the LLM for optimization.
    
    Args:
        current_code: The Solidity code to optimize
        retry_info: If None, sends first-try prompt. Otherwise, sends retry prompt with failure details.
    
    Returns:
        Cleaned diff patch string, or None on error
    """
    # Build the appropriate prompt
    if retry_info is None:
        print(">> Sending FIRST TRY request to LLM...")
        user_content = prompts.USER_PROMPT_FIRST.format(code=current_code)
    else:
        attempt = retry_info.get('attempt', '?')
        print(f">> Sending RETRY request (Attempt #{attempt})...")
        user_content = prompts.USER_PROMPT_RETRY.format(
            compile=retry_info.get('compile', 'true'),
            test=retry_info.get('test', 'NONE'),
            type=retry_info.get('type', 'unknown'),
            error=retry_info.get('error', 'Check logic'),
            trace=retry_info.get('trace', 'N/A'),
            code=current_code
        )
    
    try:
        response = client.chat.completions.create(
            model=config.MODEL_ID,
            messages=[
                {"role": "system", "content": prompts.SYSTEM_PROMPT},
                {"role": "user", "content": user_content}
            ],
            temperature=0.1,
            max_tokens=2000
        )
        
        raw_diff = response.choices[0].message.content
        clean_diff = utils.clean_llm_response(raw_diff)
        
        return clean_diff
    
    except Exception as e:
        print(f"[API ERROR]: {e}")
        return None
    
    # ... (importi ostaju isti, dodaj 're' ako fali) ...
import re
# ...

def get_equivalence_constraints(original_code: str, candidate_code: str) -> list[str]:
    """
    Salje original i kandidat LLM-u da utvrdi potrebna ogranicenja (constraints).
    Vraca listu stringova, npr. ["require(balanceOf[to] <= type(uint256).max - val);"]
    """
    print(">> [ANALYSIS] Checking equivalence and synthesizing constraints...")
    
    user_content = prompts.EQUIVALENCE_PROMPT.format(
        original_code=original_code,
        candidate_code=candidate_code
    )

    try:
        response = client.chat.completions.create(
            model=config.MODEL_ID,
            messages=[
                {"role": "user", "content": user_content} # Ovde ne koristimo system prompt nuzno
            ],
            temperature=0.1,
            max_tokens=1000
        )
        
        raw_text = response.choices[0].message.content
        
        # Parsiranje outputa: Trazimo tekst izmedju "-- Constraints (minimal) ---" i "-- Rationale ---"
        # Koristimo re.IGNORECASE da budemo robusniji
        pattern = r"-- Constraints \(minimal\) ---\s*(.*?)\s*-- Rationale ---"
        match = re.search(pattern, raw_text, re.DOTALL | re.IGNORECASE)
        
        constraints = []
        if match:
            block = match.group(1)
            lines = block.splitlines()
            for line in lines:
                clean_line = line.strip()
                # Ignorisemo prazne linije ili komentare
                if clean_line and not clean_line.startswith(("-", "//", "#", "None")):
                    # Pakujemo u require sintaksu
                    constraints.append(f"require({clean_line});")
        
        if constraints:
            print(f">> [ANALYSIS] Found {len(constraints)} constraints.")
        else:
            print(">> [ANALYSIS] Unconditional equivalence likely (no constraints found).")

        return constraints

    except Exception as e:
        print(f"[API ERROR - Analysis]: {e}")
        return []
    

# ... (Importi i postojece funkcije) ...

def get_safety_constraints(code: str) -> list[str]:
    """
    Analizira kod PRE optimizacije da bi pronasao potrebne require provere.
    """
    print(">> [SAFETY] Analyzing code for arithmetic safety constraints...")
    
    user_content = prompts.SAFETY_PROMPT.format(code=code)

    try:
        response = client.chat.completions.create(
            model=config.MODEL_ID,
            messages=[{"role": "user", "content": user_content}],
            temperature=0.1,
            max_tokens=1000
        )
        
        raw_text = response.choices[0].message.content
        
        # Parsiranje je isto kao za equivalence
        pattern = r"-- Constraints \(minimal\) ---\s*(.*?)\s*-- Rationale ---"
        match = utils.re.search(pattern, raw_text, utils.re.DOTALL | utils.re.IGNORECASE)
        
        constraints = []
        if match:
            lines = match.group(1).splitlines()
            for line in lines:
                clean = line.strip()
                if clean and not clean.startswith(("-", "//", "#")):
                    constraints.append(f"require({clean});")
        
        print(f">> [SAFETY] Found {len(constraints)} safety constraints.")
        return constraints

    except Exception as e:
        print(f"[API ERROR - Safety]: {e}")
        return []