from __future__ import annotations

import asyncio
import hashlib
import time
from pathlib import Path
from typing import Any

from .config import resolve_api_key
from .console import warn
from .utils import append_jsonl, artifact_path, ensure_dir, extract_json_object, now_iso


# Default per-request timeout (seconds). A hung/rate-limited provider connection would otherwise
# stall a worker for the SDK default (~600s). Override with provider.timeout_seconds.
DEFAULT_TIMEOUT_SECONDS = 180.0
MAX_RETRIES = 8


class ModelRouter:
    """Routes every model call to the single configured provider, retries transient failures, and
    records cost + a live llm_calls.jsonl row for each response. One AsyncOpenAI client is shared by
    all roles (they differ only by model id and decoding params, passed per call)."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self._client: Any = None
        self._fake_counter = 0
        self._fake_lock = asyncio.Lock()
        self._log_tasks: set[asyncio.Task[None]] = set()
        # Cost accounting keyed by (role, task, model) -> [calls, in_tokens, out_tokens, duration].
        self.cost: dict[tuple[str, str, str], list[float]] = {}
        self.started = time.time()
        self._output_dir = Path(config["project"]["output_dir"]) if config.get("project", {}).get("output_dir") else None
        if self._output_dir is not None:
            ensure_dir(self._output_dir)

    def model_name(self, role: str) -> str:
        return self.config["models"][role]["model"]

    async def complete(self, role: str, prompt: str, system: str, task: str = "unknown") -> str:
        model = self.config["models"][role]["model"]

        # Resolve decoding params once per call so fake and real paths log identical provenance.
        sampling, extras = resolve_sampling(self.config, role, task)

        # Fake responses stay async so tests exercise the same call shape as real runs.
        if model == "fake":
            async with self._fake_lock:
                self._fake_counter += 1
                response = fake_complete(prompt, self._fake_counter)
            self._account(role, task, model, prompt, system, response, 0.0, None, extras, sampling)
            return response

        client = self._get_client()
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        started = time.time()

        # Retry transient failures (transport errors, 5xx, 408/409/429); fail fast on other 4xx.
        for attempt in range(MAX_RETRIES):
            try:
                kwargs = {"model": model, "messages": messages, **sampling}
                if extras:
                    kwargs["extra_body"] = extras
                response = await client.chat.completions.create(**kwargs)
                content = response.choices[0].message.content or ""
                self._account(role, task, model, prompt, system, content, time.time() - started, getattr(response, "usage", None), extras, sampling)
                return content
            except Exception as exc:  # noqa: BLE001 - provider SDKs expose several exception classes.
                wait = _retry_wait(exc, attempt)
                if wait is None or attempt == MAX_RETRIES - 1:
                    raise
                await asyncio.sleep(wait)
        raise RuntimeError("unreachable: retry loop exited without returning or raising")

    async def complete_json(self, role: str, prompt: str, system: str, task: str = "unknown") -> Any:
        return extract_json_object(await self.complete(role, prompt, system, task=task))

    async def flush_logs(self) -> None:
        if not self._log_tasks:
            return
        # A failed log write must not abort shutdown, but it must not vanish silently either: the
        # llm_calls.jsonl contract says every response is logged, so report any losses on stderr.
        results = await asyncio.gather(*list(self._log_tasks), return_exceptions=True)
        failures = [r for r in results if isinstance(r, Exception)]
        if failures:
            warn(f"{len(failures)} llm_calls.jsonl log write(s) failed; some calls may be unlogged ({failures[0]}).")

    def _get_client(self) -> Any:
        # One client for the whole run: all roles share the provider's base_url, key, and timeout.
        if self._client is not None:
            return self._client
        provider = self.config.get("provider", {})
        api_key = resolve_api_key(provider.get("api_key_env", "OPENROUTER_API_KEY"))
        if not api_key:
            raise ValueError(
                f"Missing API key: put {provider.get('api_key_env', 'OPENROUTER_API_KEY')} in a .env file at the project root."
            )
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError("The openai package is required for real model calls.") from exc
        timeout = float(provider.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        self._client = AsyncOpenAI(api_key=api_key, base_url=provider["base_url"], timeout=timeout)
        return self._client

    def _account(
        self,
        role: str,
        task: str,
        model: str,
        prompt: str,
        system: str,
        response: str,
        duration: float,
        usage: Any,
        extra_body: dict[str, Any] | None,
        sampling: dict[str, Any] | None,
    ) -> None:
        # Compute tokens once (estimating when the provider omits usage) so cost accounting and
        # the llm_calls log always agree, then accumulate cost and schedule the log row.
        in_tokens = getattr(usage, "prompt_tokens", None) or max(1, len(prompt) // 4)
        out_tokens = getattr(usage, "completion_tokens", None) or max(1, len(response) // 4)
        agg = self.cost.setdefault((role, str(task), model), [0, 0, 0, 0.0])
        agg[0] += 1
        agg[1] += in_tokens
        agg[2] += out_tokens
        agg[3] += duration

        if self._output_dir is None:
            return
        row = {
            "created_at": now_iso(),
            "role": role,
            "task": str(task),
            "model": model,
            "duration_seconds": round(duration, 3),
            "system": system,
            "prompt": prompt,
            "response": response,
            "in_tokens": in_tokens,
            "out_tokens": out_tokens,
            "sampling": sampling,
            "extra_body": extra_body,
        }
        log_task = asyncio.create_task(self._write_log(row))
        self._log_tasks.add(log_task)
        log_task.add_done_callback(self._log_tasks.discard)

    async def _write_log(self, row: dict[str, Any]) -> None:
        await asyncio.to_thread(append_jsonl, artifact_path(self._output_dir, "llm_calls"), row)  # type: ignore[arg-type]


# OpenAI-compatible decoding params sent as top-level call kwargs; everything else rides extra_body.
KNOWN_PARAMS = ("temperature", "top_p", "max_tokens", "frequency_penalty", "presence_penalty", "stop", "seed")
SAMPLING_DEFAULTS = {"temperature": 0.7, "max_tokens": 32768}
# Connection/control keys on a model role are not decoding params and must not be sent as sampling kwargs.
_CONNECTION_KEYS = frozenset({"base_url", "api_key", "api_key_env", "model", "timeout_seconds", "extra_body"})


def resolve_sampling(config: dict[str, Any], role: str, task: str) -> tuple[dict[str, Any], dict[str, Any]]:
    # Layer decoding params: built-in defaults <- model-role static <- per-task overrides.
    model_cfg = config["models"][role]
    layered: dict[str, Any] = dict(SAMPLING_DEFAULTS)
    for key, value in model_cfg.items():
        if key not in _CONNECTION_KEYS:
            layered[key] = value
    task_cfg = (config.get("sampling") or {}).get("tasks") or {}
    layered.update(task_cfg.get(str(task)) or {})

    # Split known OpenAI params (top-level) from provider-specific ones (extra_body pass-through).
    call_params: dict[str, Any] = {}
    extra_overrides: dict[str, Any] = {}
    for key, value in layered.items():
        (call_params if key in KNOWN_PARAMS else extra_overrides)[key] = value

    # Per-task provider params merge on top of the role's static extra_body (task wins on conflict).
    extra_body = dict(model_cfg.get("extra_body") or {})
    extra_body.update(extra_overrides)
    return call_params, extra_body


def _retry_after_seconds(response: Any) -> float | None:
    headers = getattr(response, "headers", {}) or {}
    value = headers.get("retry-after") or headers.get("Retry-After")
    return min(60.0, float(value)) if value else None


def _retry_wait(exc: Exception, attempt: int) -> float | None:
    # Return the seconds to wait before retrying, or None to fail fast. Transient = transport errors
    # (no HTTP status), 5xx, and 408/409/429; everything else (auth/bad-request 4xx) fails fast.
    response = getattr(exc, "response", None)
    status = getattr(exc, "status_code", None) or getattr(response, "status_code", None)
    if status == 429:
        return _retry_after_seconds(response) or 2.0
    if status is None or status in {408, 409} or status >= 500:
        return min(10.0, 0.5 * 2**attempt)
    return None


def fake_complete(prompt: str, counter: int = 0) -> str:
    """Deterministic fake model used by tests and smoke examples."""
    if "Generate exactly one synthetic data point matching the meta-prompt" in prompt:
        return f"Once upon a time there was a cat with id {counter}. The cat had adventures."
    if '"factors"' in prompt:
        return '{"factors":[{"name":"topic","description":"Subject area"},{"name":"difficulty","description":"Difficulty level"}]}'
    if '"children"' in prompt:
        return '{"children":[{"name":"alpha","description":"Alpha branch"},{"name":"beta","description":"Beta branch"}]}'
    if '"plan"' in prompt:
        return '{"plan":"Expand each node into two concrete and balanced child nodes."}'
    if '"strategies"' in prompt:
        return '{"strategies":[{"id":"general","description":"Sample every taxonomy root together.","taxonomy_roots":["topic","difficulty"],"weight":1.0}]}'
    if '"meta_prompts"' in prompt:
        suffix = "alpha" if "alpha" in prompt else "beta" if "beta" in prompt else "general"
        return '{"meta_prompts":["Create a concise input and output pair about '+suffix+'.","Create a varied training example about '+suffix+'."]}'
    if '"meta_prompt"' in prompt and "Make this meta-prompt" in prompt:
        return '{"meta_prompt":"Create a nuanced but concise input and output pair about the sampled requirements."}'
    if '"verdict"' in prompt:
        return '{"verdict":"accept","explanation":"The record is valid and follows the prompt."}'
    if '"scores"' in prompt:
        return '{"scores":[{"id":"0","score":5,"reason":"Moderate complexity"}]}'
    if '"node_name"' in prompt:
        return '{"node_name":"alpha"}'
    digest = hashlib.sha1(f"{prompt}:{counter}".encode("utf-8")).hexdigest()[:8]
    return '{"input":"Question '+digest+'?","output":"Answer '+digest+' with traceable lineage."}'
