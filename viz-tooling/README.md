# viz-tooling

Standalone, single-file HTML tools for inspecting `simula` runs. No build step and no server —
open a file in a browser and drag artifacts onto it (CDN-loaded; needs internet for the libs).

- **`data_viewer.html`** — drag/drop `dataset.final.jsonl` (plus optional `cost_summary.json`,
  `strategies.json`, and the run's `*.yaml`). Paged record browser with search, strategy filter,
  accepted-only toggle, and per-row provenance.
- **`taxonomy_viewer.html`** — visualize a run's `taxonomy.json` as a tree.
- **`pipeline_explainer.html`** — an explainer of the generation pipeline (how taxonomy → strategy →
  generation → critique → evaluate fit together).

These are convenience tools, not part of the package; nothing in `simula/` depends on them.
