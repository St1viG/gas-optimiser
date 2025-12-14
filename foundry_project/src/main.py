"""
Main entry point for the Solidity gas optimizer.
"""
import sys
import os
import config
import optimizer

def main():
    if len(sys.argv) < 2:
        print("Usage: python src/main.py <input_file>")
        sys.exit(1)
    
    input_file = sys.argv[1]
    
    # Osiguraj da folder postoji (iako ga i optimizer kreira, ne skodi)
    if not os.path.exists(config.SOL_FOLDER):
        os.makedirs(config.SOL_FOLDER)
    
    try:
        print(f"[LOAD] Reading '{input_file}'...")
        with open(input_file, "r", encoding="utf-8") as f:
            original_code = f.read()
    except Exception as e:
        print(f"[ERROR] Failed to load input: {e}")
        sys.exit(1)
    
    # Uklonjen poziv save_original() jer optimizer.py sada to radi dinamicki
    
    # Pokreni optimizaciju direktno na ucitanom kodu
    result = optimizer.run_optimization_loop(original_code)
    
    if result.success:
        print("\n" + "="*50)
        print("SUCCESS! Code optimized and validated.")
        # Posto su imena dinamicka, upucujemo korisnika na folder
        print(f"Results are saved in: {config.SOL_FOLDER}")
        sys.exit(0)
    else:
        print(f"FAILED: {result.message}")
        sys.exit(1)

if __name__ == "__main__":
    main()