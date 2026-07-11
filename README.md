# Aadithya QuantLab

Aadithya QuantLab is a structured R&D repository for intraday index research, beginning with NIFTY market-state taxonomy and expanding into feature engineering, experiment design, validation, and implementation planning.

## Repository Map

- `research/papers/` - version-controlled research papers and appendices.
- `research/mathematical_specifications/` - formal definitions, notation, and derivations.
- `research/knowledge_base/` - reusable notes, assumptions, references, and decisions.
- `research/data_schema/` - canonical data contracts and dataset documentation.
- `research/architecture/` - system design notes for research and production integration.
- `research/implementation_roadmap/` - staged delivery plans and acceptance criteria.
- `features/library/` - feature specifications, lineage, and implementation status.
- `experiments/library/` - experiment protocols, run manifests, and outcomes.
- `src/aadithya_quantlab/` - Python research modules.
- `validation/` - validation framework, reports, and evidence packs.
- `tests/` - automated tests for research code and validation utilities.

## Research Program

The project treats each paper as an executable research unit:

1. Define a market problem and formal vocabulary.
2. Convert concepts into mathematical specifications.
3. Register derived features in the feature library.
4. Register experiments with datasets, metrics, and falsification tests.
5. Promote validated outputs into the architecture and implementation roadmap.

Paper 001 starts the program with a taxonomy for intraday NIFTY market states.

## Quick Commands

Run EXP-001 on a canonical intraday CSV:

```powershell
py -m aadithya_quantlab.experiments.run_exp001 --input data/raw/nifty_intraday.csv --output validation/reports/EXP-001_real_nifty_report.md --labeled-output validation/reports/EXP-001_real_nifty_labeled.csv
```

Record a Zerodha-shaped paper order:

```powershell
py -m aadithya_quantlab.trading.run_paper_order --symbol NIFTY26JUL24000CE --side BUY --qty 65
```

Paper-order commands write to a local ledger only; they do not place live broker orders. NIFTY paper orders are currently locked to quantity `65`.
