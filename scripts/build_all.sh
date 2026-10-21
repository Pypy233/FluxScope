#!/usr/bin/env bash
# Rebuild every deliverable from sources: viewer, reports for the corpus, schema, evaluation, tests.
# LLM results are replayed from the recorded responses in eval/llm-records (no model is called).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-.venv/bin/python}

$PY -m fluxscope schema
$PY frontend/build.py --check
$PY -m fluxscope validate examples/*.example.json tests/fixtures/unseen-topology.json

if [ ! -f corpus/kaia-yield/src/superearn/core/CooldownVault.sol ]; then
    $PY scripts/fetch_snapshot.py corpus/kaia-yield   # re-create the pinned snapshot from upstream
fi

mkdir -p dist/reports
for p in beefy-erc4626 kaia-yield metamorpho fixtures/receiver-vault fixtures/oz-erc4626; do
    name=$(basename "$p")
    $PY -m fluxscope analyze "corpus/$p" --quiet --json "dist/reports/$name.json" --html "dist/reports/$name.html"
done
# rules + recorded LLM proposals (first recorded sample)
for p in beefy-erc4626 kaia-yield metamorpho; do
    $PY -m fluxscope analyze "corpus/$p" --quiet --llm replay --llm-cache "eval/llm-records/$p" \
        --json "dist/reports/$p.llm.json" --html "dist/reports/$p.llm.html"
done

cp dist/reports/beefy-erc4626.json tests/fixtures/beefy-erc4626.result.json
$PY scripts/build_site.py
for p in beefy-erc4626 kaia-yield metamorpho; do
    $PY -m fluxscope evaluate "eval/$p.truth.json" --configs rules,llm-only,combined --runs 3 \
        --llm replay --llm-cache "eval/llm-records/$p" --out "eval/results/$p.json"
done
$PY scripts/eval_summary.py > eval/results/summary.md
$PY -m pytest -q "$@"
