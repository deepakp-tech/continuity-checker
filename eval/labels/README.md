# Hand labels (kept out of git)

`run_evals.py --suite pack` writes three templates here (or to `--labels`):

| Template | Fill in | Gives you |
|---|---|---|
| `pack_alias_gold_TEMPLATE.csv` | `character` for every speaker label (same text = same person) | name-merge precision, recall, wrong merges |
| `facts_sample_<system>_TEMPLATE.csv` | `label` = supported / misread / invented for 50 random facts | extraction precision |
| `judge_pairs_<system>_TEMPLATE.csv` | `gold_verdict` = contradiction / story_change / intentional / extraction_error | judge accuracy, confusion matrix, twist false-alarm rate |

Save each filled file **without** `_TEMPLATE` in the name and re-run the evals.
These files quote the confidential scripts, so `.gitignore` excludes them.
