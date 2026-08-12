from . import config
from . import llm_client
from . import utils
from . import validator

class OptimizationResult:
    def __init__(self, success: bool, message: str, optimized_code: str | None = None):
        self.success = success
        self.message = message
        self.optimized_code = optimized_code

def run_optimization_loop(original_code: str) -> OptimizationResult:
    """
    Main Loop:
    1. Determine contract name.
    2. Save original as {Name}.sol.
    3. LLM loop -> Save candidate as {Name}Candidate.sol.
    4. Validate using specific paths.
    """
    current_code = original_code
    retry_info = None
    
    # 1. Odredi ime ugovora
    contract_name = utils.extract_contract_name(original_code)
    print(f"[INFO] Detected Contract Name: {contract_name}")
    
    # 2. Definisi putanje
    config.SOL_FOLDER.mkdir(parents=True, exist_ok=True)

    original_path = config.SOL_FOLDER / f"{contract_name}.sol"
    candidate_path = config.SOL_FOLDER / f"{contract_name}Candidate.sol"

    # 3. Sacuvaj original
    original_path.write_text(original_code, encoding="utf-8")
    print(f"[INFO] Saved original to: {original_path}")

    for attempt in range(1, config.MAX_RETRIES + 1):
        print(f"\n" + "="*50)
        print(f"ATTEMPT {attempt}/{config.MAX_RETRIES}")
        print("="*50)
        
        print(f"[1] Calling LLM (Context: {'Retry' if retry_info else 'Fresh'})...")
        diff = llm_client.get_optimization_proposal(current_code, retry_info)
        
        if not diff:
            print("[ERROR] LLM returned empty response.")
            return OptimizationResult(False, "LLM returned no response")
        
        print("[2] Applying patch...")
        candidate_code, patch_error = utils.apply_patch(original_code, diff)
        
        if patch_error:
            print(f"[2] PATCH FAILED: {patch_error}")
            retry_info = {
                "compile": "false", 
                "test": "N/A", 
                "type": "patch_apply_error",
                "error": f"Could not apply patch: {patch_error}", 
                "trace": "Diff was invalid or context mismatch."
            }
            continue 
        
        # 4. Sacuvaj kandidata pod svojim imenom ugovora, da ne pregazi original
        candidate_code = utils.rename_contract(
            candidate_code, contract_name, f"{contract_name}Candidate"
        )
        candidate_path.write_text(candidate_code, encoding="utf-8")


        print(f"[3] Validating candidate at: {candidate_path}...")
        # Pozivamo validator sa dinamičkim putanjama
        is_valid, failure_data = validator.validate(original_path, candidate_path)
        
        if is_valid:
            print(f"\n[SUCCESS] Optimization verified at attempt {attempt}!")
            return OptimizationResult(True, "Optimization successful!", candidate_code)
        
        print(f"[4] VALIDATION FAILED.")
        print(f"    Type: {failure_data.get('type')}")
        print(f"    Error: {failure_data.get('error')}")
        
        retry_info = failure_data
        retry_info['attempt'] = attempt
    
    return OptimizationResult(False, f"Max retries ({config.MAX_RETRIES}) reached without success.")