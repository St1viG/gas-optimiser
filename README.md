# AI-Powered Solidity Gas Optimizer

This tool automates the gas optimization of Solidity smart contracts using Large Language Models (LLMs). It employs a rigorous "Generate & Verify" loop to ensure that optimizations reduce gas costs without breaking the contract's business logic or introducing security vulnerabilities.

## 🚀 Features

- **Automated Optimization**: Uses an LLM (HuggingFace Inference API) to suggest code improvements.
- **Verification-First Approach**:
  - **Fuzz Testing**: Automatically generates and runs Foundry fuzz tests to ensure state consistency.
  - **Symbolic Execution**: Uses `hevm` to formally prove bytecode equivalence between the original and optimized contract.
- **Gas Reporting**: Compares gas usage before and after optimization.
- **Retry Logic**: Automatically retries with the LLM if the generated patch fails verification or compilation.

## 🛠 Prerequisites

Ensure you have the following installed:

1.  **Python 3.10+**
2.  **Foundry** (for `forge`):
    ```bash
    curl -L [https://foundry.paradigm.xyz](https://foundry.paradigm.xyz) | bash
    foundryup
    ```
3.  **Hevm** (Optional but recommended for symbolic checking):
    ```bash
    curl -L [https://github.com/ethereum/hevm/releases/download/v0.53.0/hevm-0.53.0-linux-x64-musl.tar.gz](https://github.com/ethereum/hevm/releases/download/v0.53.0/hevm-0.53.0-linux-x64-musl.tar.gz) | tar xz
    mv hevm /usr/local/bin/
    ```

## 📦 Installation

1.  Clone the repository:
    ```bash
    git clone [https://github.com/your-username/gas-optimizer.git](https://github.com/your-username/gas-optimizer.git)
    cd gas-optimizer/foundry_project
    ```

2.  Install Python dependencies:
    ```bash
    pip install -r requirements.txt
    ```
    *(If `requirements.txt` is missing, install manually: `pip install huggingface_hub python-dotenv`)*

3.  Set up environment variables:
    Create a `.env` file in the `foundry_project` root and add your Hugging Face token:
    ```env
    HF_TOKEN=hf_your_token_here
    MODEL_NAME=meta-llama/Meta-Llama-3-70B-Instruct  # or your preferred model
    ```

## 🚀 Usage

To optimize a Solidity contract, run the `run.py` script with the path to your target contract file:

```bash
python3 run.py test-cases/tc0.txt
