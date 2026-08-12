"""
Utility functions for the Solidity optimizer.
"""
import re

def clean_llm_response(response_text: str) -> str:
    """Removes markdown code block markers."""
    pattern = r"```(?:diff)?\s*(.*?)\s*```"
    match = re.search(pattern, response_text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return response_text.strip()

def strip_inline_comments(line: str) -> str:
    """
    Removes inline comments added by LLM from a code line.
    """
    llm_comment_patterns = [
        r'\s*//\s*Ensure.*$',
        r'\s*//\s*This.*$',
        r'\s*//\s*Safe because.*$',
        r'\s*//\s*No overflow.*$',
        r'\s*//\s*Cannot overflow.*$',
        r'\s*//\s*Overflow.*$',
        r'\s*//\s*Gas optimization.*$',
        r'\s*//\s*Optimized.*$',
        r'(\s*//[^/].*){2,}',
    ]
    
    result = line
    for pattern in llm_comment_patterns:
        result = re.sub(pattern, '', result, flags=re.IGNORECASE)
    
    return result

def normalize_string(s: str) -> str:
    """Removes all whitespace to allow fuzzy matching."""
    return "".join(s.split())

def extract_contract_name(code: str) -> str:
    """Extracts the contract name from Solidity source code."""
    match = re.search(r'contract\s+(\w+)', code)
    if match:
        return match.group(1)
    return "UnknownContract"

def rename_contract(code: str, old_name: str, new_name: str) -> str:
    """Renames a contract declaration, e.g. `contract X` -> `contract XCandidate`.

    The candidate must not share a contract name with the original: the validator
    derives Foundry filenames from the declared name, so identical names make the
    candidate overwrite the original.
    """
    return re.sub(rf'\bcontract\s+{re.escape(old_name)}\b',
                  f'contract {new_name}', code)

def apply_patch(original_code: str, diff_text: str) -> tuple[str | None, str | None]:
    """
    Applies a unified diff fuzzily. 
    """
    clean_diff = clean_llm_response(diff_text)
    lines = clean_diff.splitlines()
    
    hunks = []
    current_hunk = {"remove": [], "add": []}
    in_hunk = False

    for line in lines:
        if line.startswith("---") or line.startswith("+++") or line.startswith("diff") or line.startswith("index"):
            continue

        if line.startswith("@@"):
            if in_hunk and (current_hunk["remove"] or current_hunk["add"]):
                hunks.append(current_hunk)
                current_hunk = {"remove": [], "add": []}
            in_hunk = True
            continue
            
        if not in_hunk and (line.startswith("-") or line.startswith("+")):
            in_hunk = True

        if line.startswith("-"):
            current_hunk["remove"].append(line[1:])
        elif line.startswith("+"):
            clean_line = strip_inline_comments(line[1:])
            current_hunk["add"].append(clean_line)
    
    if current_hunk["remove"] or current_hunk["add"]:
        hunks.append(current_hunk)

    if not hunks:
        return None, "No valid hunks found in diff"

    modified_code = original_code

    for i, hunk in enumerate(hunks):
        search_lines = hunk["remove"]
        replace_lines = hunk["add"]

        if not search_lines:
            continue 

        # 1. Exact match
        search_block = "\n".join(search_lines)
        if search_block in modified_code:
            replace_block = "\n".join(replace_lines)
            modified_code = modified_code.replace(search_block, replace_block, 1)
            continue

        # 2. Fuzzy match
        original_lines = modified_code.splitlines()
        best_match_index = -1
        
        norm_search = [normalize_string(l) for l in search_lines if l.strip()]
        if not norm_search:
            continue

        for line_idx in range(len(original_lines)):
            match = True
            for offset, s_line in enumerate(norm_search):
                if line_idx + offset >= len(original_lines):
                    match = False
                    break
                if normalize_string(original_lines[line_idx + offset]) != s_line:
                    match = False
                    break
            
            if match:
                best_match_index = line_idx
                break
        
        if best_match_index != -1:
            before = original_lines[:best_match_index]
            after = original_lines[best_match_index + len(search_lines):]
            new_code_lines = before + replace_lines + after
            modified_code = "\n".join(new_code_lines)
        else:
            return None, f"Could not find code block for hunk #{i+1}."

    return modified_code, None