Rules-only (no LLM) outputs on the two public synthetic shows, produced by:

    python -m cc run --input eval/synthetic/<show> --out <dir> --genre <genre> --rules-only
    python eval/run_evals.py --suite synthetic --rules-only

These are the offline baseline. Re-run with your API keys (drop --rules-only) to get the LLM results,
and with --compare-rules on run_evals.py to get both columns side by side.
