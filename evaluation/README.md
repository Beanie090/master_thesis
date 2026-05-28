# LEAi long-term memory evaluation

This folder contains a small multi-session stress test for the LEAi thesis evaluation.

## Files

- `scenarios.json`: 10 scripted long-term memory scenarios.
- `run_long_term_memory_eval.py`: Flask `test_client` runner that sends the scenarios through `/start`, `/chat`, `/load_session`, then asks the recall question.
- `results/`: generated after running the script.

## How to run

From the repository root:

```bash
python evaluation/run_long_term_memory_eval.py
```

If `app.py` is inside a subfolder such as `LEAi/`, either run from the repo root or set:

```bash
LEAI_APP_DIR=/path/to/master_thesis/LEAi python evaluation/run_long_term_memory_eval.py
```

For a quick test with fewer API calls:

```bash
python evaluation/run_long_term_memory_eval.py --limit 1 --max-distractors 3
```

## Outputs

The script writes:

- `evaluation/results/long_term_memory_results.json`
- `evaluation/results/long_term_memory_results.csv`
- `evaluation/results/summary.md`

## Important note

The automatic grading is keyword-based. Use it as a first pass, then manually inspect partial matches or hallucination flags before reporting final thesis results.
