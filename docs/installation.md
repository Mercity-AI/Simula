# Installation

`simula` needs Python 3.11 or newer.

```bash
pip install simula-gen
```

The package on PyPI is `simula-gen`; the command it installs is `simula`. Check it works:

```bash
simula --help
```

## From source

The PyPI release can trail the repository. If a key documented here is rejected as unknown, install
from source instead:

```bash
git clone https://github.com/Mercity-AI/Simula.git
cd Simula
pip install -e .
```

A source checkout also gives you the [examples](examples.md). Without installing, you can run the CLI
from the checkout as `python -m simula.cli`.

## Optional extras

| Extra | Install | For |
|---|---|---|
| `diversity` | `pip install "simula-gen[diversity]"` | Embedding-based diversity metrics in `evaluate`. Pulls in numpy, scikit-learn and sentence-transformers (and PyTorch), so it is large. |
| `dev` | `pip install -e ".[dev]"` (source checkout) | Running the test suite: `pytest -q`. |

The base install is light: an OpenAI client, Pydantic, PyYAML, jsonschema, python-dotenv and rich.

## API key

Real runs need a key for an OpenAI-compatible provider, OpenRouter by default. Put it in a `.env` file in
the directory you will run `simula` from:

```bash
echo 'OPENROUTER_API_KEY=sk-or-...' > .env
```

The `.env` file is the only place the key is read from; a variable exported in your shell is ignored.
Keep `.env` out of version control. The offline [Quickstart](quickstart.md) run needs no key. To use
another provider, see [Providers and models](guides/providers-and-models.md).
