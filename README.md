# Verification Coverage Analyzer with LLM Integration

An automated Functional Coverage Analysis agent designed for ASIC/SoC chip verification teams. The system parses functional coverage reports, identifies coverage holes and cross-coverage gaps, analyzes what is working vs. what is failing, synthesizes directed test scenarios using Google Gemini (with 30 RPM rate limiting and few-shot prompting), prioritizes suggestions using a mathematical scoring formula, and predicts time to coverage closure.

---

## 💡 What Does This Tool Do? (In Layman's Terms)

Think of this tool as an **automated chip inspector**:
1. **Reads Coverage Reports:** It reads simulation reports (`sample_report.txt`) to see how much of a hardware design was successfully tested.
2. **Finds Missing Scenarios:** It pinpoints the exact hardware states that were missed (the "coverage holes").
3. **Generates Actionable Test Steps:** It passes the missing holes to an AI model (like Google Gemini Flash) alongside examples of what is already working, asking it to write a concrete 4-step test sequence.
4. **Ranks by Priority:** It uses a mathematical formula to calculate a **Priority Score**—putting the easiest tests that give the biggest coverage improvements at the top of the list.
5. **Forecasts Project Closure:** It calculates the probability of reaching 100% coverage and flags any tests blocked by missing hardware or testbench equipment.

---

## 🛠️ Local Setup Guide (Step-by-Step in Layman's Terms)

### Step 1: Open Your Terminal
Open PowerShell, Command Prompt, or your terminal, and navigate to the project directory:
```bash
cd "c:\Users\Puneet Rai\Downloads\Agent"
```

### Step 2: Create & Activate a Virtual Environment
A virtual environment keeps the project dependencies isolated and clean:

**On Windows (PowerShell):**
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```
*(If script execution is disabled on PowerShell, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` or activate with `.\venv\Scripts\activate.bat` in Command Prompt).*

**On macOS / Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

### Step 3: Install Required Dependencies
Install the lightweight requirements:
```bash
pip install -r requirements.txt
```

### Step 4: Configure Your API Key (Optional)
The project works out of the box with zero setup in **Offline Expert Mode**.

To connect **Google Gemini Free Tier**:
1. Create a `.env` file from the example template:
   ```powershell
   copy .env.example .env
   ```
2. Open `.env` and add your Google Gemini API key:
   ```ini
   GEMINI_API_KEY=your_actual_api_key_here
   LLM_PROVIDER=gemini
   LLM_MODEL=gemini-f3.5-flash-latest
   GEMINI_RPM=30
   ```
*(Note: Your `.env` file is protected by `.gitignore` and will never be committed or uploaded to Git).*

---

## 🚀 How to Run (Copy-Paste Commands)

### 1. The Simplest Run (One-Command Run)
Runs the tool on the primary DMA controller coverage report:
```powershell
python main.py
```
> If you have a `GEMINI_API_KEY` in `.env`, it automatically uses Gemini with 30 RPM rate limiting. If no key is set, it automatically uses the built-in domain expert engine.

---

### 2. Run with Google Gemini Free Tier Explicitly
Paces calls at 30 RPM (1 call every 2.0s) so your free-tier quota is never throttled:
```powershell
python main.py --provider gemini --model gemini-f3.5-flash-latest --rpm 30 --report examples/sample_report.txt
```

---

### 3. Save Results to a Structured JSON File
Saves all parsed data, test outlines, priorities, and closure predictions to a JSON file:
```powershell
python main.py --report examples/sample_report.txt --json-out my_results.json
```

---

### 4. Test Different Hardware IP Use Cases

**A. DMA Controller (Assignment Specification):**
```powershell
python main.py --report examples/sample_report.txt
```

**B. AXI Interconnect Crossbar:**
```powershell
python main.py --report examples/sample_report_2.txt
```

**C. SPI Master Controller (Brand-New IP):**
```powershell
python main.py --report examples/sample_report_3.txt
```

---

### 5. Verify Live Gemini API & Few-Shot Prompting
Run the diagnostic script to see live API round-trip latency, few-shot prompt injection, and rate-limiter pacing:
```powershell
python verify_gemini_live.py
```

---

### 6. Run the Automated Unit Test Suite
Verify that all 19 unit tests pass:
```powershell
python -m unittest discover tests
```
**Expected Output:**
```text
...................
----------------------------------------------------------------------
Ran 19 tests in 0.157s

OK
```

---

## 📐 Prioritization Algorithm

Every suggestion is ranked using the assignment's official formula:

$$\text{Priority Score} = (\text{Coverage Impact} \times 0.4) + (\text{Inverse Difficulty} \times 0.3) + (\text{Dependency Score} \times 0.3)$$

Where:
- **Coverage Impact:** Normalized score measuring primary bin resolution plus all correlated cross-coverage holes unlocked.
- **Inverse Difficulty:** $\frac{1}{\text{difficulty}}$
  - `easy` (1) $\to 1.0$
  - `medium` (2) $\to 0.5$
  - `hard` (3) $\to 0.3333$
- **Dependency Score:**
  - $1.0$ if no external dependencies exist (can run immediately).
  - $0.5$ if external dependencies exist (requires testbench updates or error injectors).

### Example Output Table

| Rank | Priority Score | Target Bin | Diff | Metric Breakdown | Action Plan |
| :---: | :---: | :--- | :---: | :---: | :--- |
| **1** | **0.6500** | `cg_transfer_size.cp_size.max` | **easy** | $0.12 \times 0.4 + 1.0 \times 0.3 + 1.0 \times 0.3$ | Max 4096-byte transfer on idle channel (No external dependencies). |
| **2** | **0.5000** | `cg_transfer_size.cp_burst_type.wrap` | **medium** | $0.50 \times 0.4 + 0.5 \times 0.3 + 0.5 \times 0.3$ | Wrap burst near boundary 0xFFC (Unlocks 3 cross-coverage holes). |
| **3** | **0.3000** | `cg_error_scenarios.cp_error_type.decode_error` | **hard** | $0.12 \times 0.4 + 0.33 \times 0.3 + 0.5 \times 0.3$ | Inject decode error into unmapped region (Requires error injector VIP). |

---

## 🔮 Coverage Closure Prediction (Bonus Part 4)

The built-in prediction model evaluates:
1. **Coverage Gap:** Current coverage vs. 100% target.
2. **Closure Probability:** Likelihood of hitting 100% with current testbench capabilities.
3. **Estimated Engineering Time & Test Iterations:** Forecast of simulation runs and hours needed.
4. **Blocking Bin Identification:** Automatically identifies bins physically impossible to close without adding new VIP or hardware capabilities (e.g., DECERR address decoders or slave stall injectors).

---

## 📁 Repository Structure

```
Agent/
├── .env.example            # Environment variables template
├── .gitignore              # Ignores .env, venv/, caches, and temp outputs
├── README.md               # Complete setup, usage, and architecture documentation
├── requirements.txt        # Minimal required dependencies
├── main.py                 # Interactive CLI tool with Rich UI & JSON export
├── verify_gemini_live.py   # Diagnostic script verifying live Gemini API & few-shot prompting
├── app/
│   ├── __init__.py
│   ├── models.py           # Pydantic data schemas
│   ├── parser.py           # Regex & state-based report parser
│   ├── analyzer.py         # Coverage hole analyzer & context extraction
│   ├── llm_generator.py    # Multi-backend LLM generator (Gemini/OpenAI/Claude/Ollama/Offline)
│   ├── prioritizer.py      # Official prioritization algorithm
│   └── predictor.py        # Part 4 (Bonus): Coverage closure prediction model
├── docs/
│   └── design_document.md  # 1-2 page architectural design document
├── examples/
│   ├── sample_report.txt   # Primary assignment input (dma_controller)
│   ├── sample_report_2.txt # Second realistic report (axi_interconnect_crossbar)
│   ├── sample_report_3.txt # Third realistic report (spi_master_controller)
│   ├── sample_output_1.json# Exported JSON for DMA controller
│   ├── sample_output_2.json# Exported JSON for AXI Interconnect
│   └── sample_output_3.json# Exported JSON for SPI Master
└── tests/
    ├── __init__.py
    ├── test_parser.py      # Parser accuracy tests
    ├── test_analyzer.py    # Context extraction tests
    ├── test_prioritizer.py # Priority scoring formula tests
    ├── test_predictor.py   # Closure velocity & blocking bin tests
    └── test_llm_generator.py# LLM generator, 30 RPM rate limiter, and caching tests
```

---

## 🛡️ Git & Submission Hygiene
A complete `.gitignore` is included to ensure:
- Secrets & API keys in `.env` are **never** committed or exposed.
- Virtual environment folders (`venv/`, `.venv/`) are excluded.
- Cache files (`.cache/`, `__pycache__/`) and local temporary files (`my_results.json`) are ignored.
- All code, examples, tests, documentation, and sample outputs are clean and ready for submission.
