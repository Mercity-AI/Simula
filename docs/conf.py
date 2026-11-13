# Sphinx config for the simula docs. Pages are Markdown (MyST); Read the Docs builds them from
# .readthedocs.yaml. Local preview: pip install -r docs/requirements.txt sphinx-autobuild, then
# sphinx-autobuild docs docs/_build/html
project = "simula"
author = "Mercity"
copyright = "2026, Mercity"

extensions = ["myst_parser", "sphinxcontrib.mermaid"]
myst_enable_extensions = ["colon_fence"]  # ::: blocks for notes and warnings
myst_heading_anchors = 3                  # GitHub-style #section links
myst_fence_as_directive = ["mermaid"]     # plain ```mermaid fences render here and on GitHub
exclude_patterns = ["_build"]

html_theme = "furo"
html_title = "simula"
html_theme_options = {
    "source_repository": "https://github.com/Mercity-AI/Simula/",
    "source_branch": "main",
    "source_directory": "docs/",
}
