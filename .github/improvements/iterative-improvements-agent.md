---
agent: 'agent'
description: 'automatically run, verify, and fix the job search workflow until all issues are resolved'
---

## Task

Run the workflow,verify issues,fix them.Keep going till no issues are found.


## Continuous improvement Strategy
1. Trigger & Execution

The agent triggers on workflow runs (push, PR, or manual dispatch).
It runs the entire job search workflow.

2. **Issue Detection**

- Table formatting: Checks for empty rows/columns, missing inline rows/columns, empty cells.
- Language recognition: Validates language detection on sample inputs.
- Scrape yield: Verifies scraping produces sufficient and valid results.
- Ranking: Confirms ranking output exists and is correctly ordered.
- Sources: Ensures all data sources produce results; identifies failing sources.

3. **Opportunistic Fixing**

- If issues are detected, the agent attempts fixes such as:
- Re-running scrapers or parsers.
- Correcting table formatting automatically.
- Adjusting language detection parameters or retraining models.
- Recalculating rankings.
- Re-fetching or repairing missing source data.

4. **Testing**

- Adds or runs tests covering the detected issues.
- Ensures tests pass before proceeding.

5. **Commit & Push**

- Commits any fixes or test additions to the repository.
- Pushes changes to trigger a new workflow run.

6. **Loop & Termination**

- Repeats the run-check-fix-test-push cycle until:
 - No issues remain, or
 - A maximum retry count is reached (to avoid infinite loops).
- If max retries reached, alerts maintainers for manual intervention.