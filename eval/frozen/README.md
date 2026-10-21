# Frozen annotations

Copies of the ground-truth annotations as they were frozen, before the first analyzer or LLM run
on each project (`eval/<project>.truth.json` are the live files: the same annotation plus
naming-only `aliases` that map analyzer ids to the annotation vocabulary).

The second real project is published under a neutral name ("Kaia yield aggregator"); in these
copies the project name was replaced in the fields below and nothing else was changed. The
SHA-256 of the files at freeze time is kept for the record.

The tool was called AssetLens when the annotations were written; it is now FluxScope. The old
name in `annotated_by` (here and in the live `eval/*.truth.json`) is left as it was.

| file | SHA-256 at freeze time | SHA-256 of this copy | replaced text |
|---|---|---|---|
| `kaia-yield.truth.frozen.json` | `0beec1a4da23d365d89ab65e24d4c6b4c7379422a5dd1a0aa4b43fb57dc69f13` | `d92991d0d317177e1257611f5789d86f6ee40ef816f79491098c59d7b5a749df` | project name in `project`; project name in `notes` |
| `metamorpho.truth.frozen.json` | `a91ad6268038845f77a6dfba18328321181edf2e6ed29b28e1da7b484b28f45a` | `4ce7d3771dc0e7f12e26d35f103d7fc8fe37aa9338c283d6cdb2b7c14c039666` | project name in `annotated_by` |
| Beefy (`eval/beefy-erc4626.truth.json`) | not frozen separately: written during the first milestone, one documented post-hoc addition (strategy ERC-4626 share exits) | | |
