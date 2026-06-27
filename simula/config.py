from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from . import prompts
from .console import warn
from .data_models import Config, TaskType  # noqa: F401 - re-exported for the rest of the package
from .utils import ensure_dir


def resolve_api_key(api_key_env: str) -> str | None:
    # The project-root .env is the ONLY source of API keys: we read it directly with dotenv_values
    # (not load_dotenv + os.getenv), so a shell-exported variable is deliberately ignored. Keep the
    # secret out of the config object/logs — it is read here only when a real client is built.
    return dotenv_values(Path.cwd() / ".env").get(api_key_env)


def load_config(path: str | Path) -> Config:
    # Load YAML, validate via the Pydantic Config model (defaults + ranges + enums live there),
    # attach the prompt set, check the optional schema subset, then ensure the output dir exists.
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    # The prompt module is a runtime object, not validated config data, so load it separately.
    prompts_ref = raw.pop("prompts", None)
    try:
        cfg = Config.model_validate({**raw, "path": config_path})
    except ValidationError as exc:
        # Present a single ValueError to callers; the field path + reason stay in the message.
        raise ValueError(str(exc)) from exc

    cfg.prompts = prompts.load_prompt_set(config_path, prompts_ref)
    if cfg.record_schema is not None:
        validate_schema_subset(cfg.record_schema)
    _warn_missing_api_key(cfg)
    ensure_dir(cfg.output_dir)
    return cfg


def _warn_missing_api_key(cfg: Config) -> None:
    # Warn (don't fail — keep `validate` offline-friendly) when a real run has no key in .env.
    roles = ("strategic", "bulk", "critic")
    if all(getattr(cfg.models, role).model == "fake" for role in roles):
        return
    if resolve_api_key(cfg.provider.api_key_env) is None:
        warn(
            f"no API key resolved from .env (api_key_env={cfg.provider.api_key_env}); "
            "real model calls will fail. Put the key in a .env file at the project root."
        )


def validate_schema_subset(schema: dict[str, Any]) -> None:
    # Keep the supported JSON Schema surface deliberately small and testable.
    Draft202012Validator.check_schema(schema)
    allowed = {"object", "string", "number", "integer", "boolean", "array"}

    def walk(node: dict[str, Any], path: str) -> None:
        node_type = node.get("type")
        if node_type not in allowed:
            raise ValueError(f"Unsupported schema type at {path}: {node_type}")
        if "enum" in node and not isinstance(node["enum"], list):
            raise ValueError(f"enum must be a list at {path}")
        if node_type == "object":
            props = node.get("properties", {})
            if not isinstance(props, dict):
                raise ValueError(f"object properties must be a mapping at {path}")
            for name, child in props.items():
                walk(child, f"{path}.{name}")
        if node_type == "array":
            if "items" not in node:
                raise ValueError(f"array schema requires items at {path}")
            walk(node["items"], f"{path}[]")

    walk(schema, "$")
