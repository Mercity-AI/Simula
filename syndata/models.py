from __future__ import annotations

import asyncio
import hashlib
import os
import time
from pathlib import Path
from typing import Any

from .cost import CostStore, record_cost
from .utils import append_jsonl, artifact_path, ensure_dir, extract_json_object, now_iso


# Default per-request timeout (seconds). A hung/rate-limited provider connection would otherwise
# stall a worker for the SDK default (~600s). Override per role with models.<role>.timeout_seconds.
DEFAULT_TIMEOUT_SECONDS = 180.0


class ModelRouter:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self._clients: dict[str, Any] = {}
        self._fake_counter = 0
        self._fake_lock = asyncio.Lock()
        self._pace_lock = asyncio.Lock()
        self._next_slot = 0.0
        self._log_tasks: set[asyncio.Task[None]] = set()
        self.cost: CostStore = {}
        self.started = time.time()
        self._output_dir = Path(config["project"]["output_dir"]) if config.get("project", {}).get("output_dir") else None
        if self._output_dir is not None:
            ensure_dir(self._output_dir)

    def model_name(self, role: str) -> str:
        return self.config["models"][role]["model"]

    async def complete(self, role: str, prompt: str, system: str | None = None, task: str = "unknown") -> str:
        model_cfg = self.config["models"][role]
        model = model_cfg["model"]

        # Resolve decoding params once per call so fake and real paths log identical provenance.
        sampling, extras = resolve_sampling(self.config, role, task)

        # Fake responses stay async so tests exercise the same call shape as real runs.
        if model == "fake":
            async with self._fake_lock:
                self._fake_counter += 1
                response = fake_complete(prompt, self._fake_counter)
            self._record_cost(role, task, model, prompt, response, 0.0, None)
            self._schedule_log(role, task, model, prompt, system, response, 0.0, None, extras, sampling)
            return response

        client = self._client_for(role, model_cfg)
        messages = [
            {"role": "system", "content": system or "You are a helpful assistant."},
            {"role": "user", "content": prompt},
        ]
        started = time.time()
        last_rate_limit = None

        # Retry transient provider failures, preserving Retry-After when available.
        for attempt in range(8):
            try:
                await self._pace(float(model_cfg.get("min_interval_seconds", 0.0)))
                kwargs = {"model": model, "messages": messages, **sampling}
                if extras:
                    kwargs["extra_body"] = extras
                response = await client.chat.completions.create(**kwargs)
                content = response.choices[0].message.content or ""
                duration = time.time() - started
                usage = getattr(response, "usage", None)
                self._record_cost(role, task, model, prompt, content, duration, usage)
                self._schedule_log(role, task, model, prompt, system, content, duration, usage, extras, sampling)
                return content
            except Exception as exc:  # noqa: BLE001 - provider SDKs expose several exception classes.
                retry_after, body = _rate_limit_details(exc)
                if retry_after is not None:
                    last_rate_limit = body
                    await asyncio.sleep(retry_after)
                    continue
                if attempt == 7:
                    raise
                await asyncio.sleep(min(2.0, 0.25 * (2**attempt)))
        raise RuntimeError(f"Rate limit retries exhausted: {last_rate_limit or 'no response body'}")

    async def complete_json(self, role: str, prompt: str, system: str | None = None, task: str = "unknown") -> Any:
        return extract_json_object(await self.complete(role, prompt, system=system, task=task))

    async def flush_logs(self) -> None:
        if not self._log_tasks:
            return
        await asyncio.gather(*list(self._log_tasks), return_exceptions=True)

    def _client_for(self, role: str, model_cfg: dict[str, Any]) -> Any:
        if role in self._clients:
            return self._clients[role]
        api_key = model_cfg.get("api_key") or os.getenv(model_cfg.get("api_key_env", ""))
        if not api_key:
            raise ValueError(f"Missing API key for model role {role}. Set api_key or api_key_env.")
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError("The openai package is required for real model calls.") from exc
        timeout = float(model_cfg.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        self._clients[role] = AsyncOpenAI(api_key=api_key, base_url=model_cfg["base_url"], timeout=timeout)
        return self._clients[role]

    async def _pace(self, min_interval_seconds: float) -> None:
        # Reserve a staggered start slot under the lock, then sleep OUTSIDE it. Holding the lock
        # across the sleep would serialize every concurrent worker; reserving slots spaces calls
        # by min_interval_seconds while keeping them in flight concurrently.
        if min_interval_seconds <= 0:
            return
        async with self._pace_lock:
            start_at = max(time.time(), self._next_slot)
            self._next_slot = start_at + min_interval_seconds
        delay = start_at - time.time()
        if delay > 0:
            await asyncio.sleep(delay)

    def _record_cost(self, role: str, task: str, model: str, prompt: str, response: str, duration: float, usage: Any) -> None:
        in_tokens = getattr(usage, "prompt_tokens", None) or max(1, len(prompt) // 4)
        out_tokens = getattr(usage, "completion_tokens", None) or max(1, len(response) // 4)
        record_cost(self.cost, role, str(task), model, in_tokens, out_tokens, duration)

    def _schedule_log(
        self,
        role: str,
        task: str,
        model: str,
        prompt: str,
        system: str | None,
        response: str,
        duration: float,
        usage: Any,
        extra_body: dict[str, Any] | None,
        sampling: dict[str, Any] | None,
    ) -> None:
        if self._output_dir is None:
            return
        task_obj = asyncio.create_task(
            self._log_call_async(role, task, model, prompt, system, response, duration, usage, extra_body, sampling)
        )
        self._log_tasks.add(task_obj)
        task_obj.add_done_callback(self._log_tasks.discard)

    async def _log_call_async(
        self,
        role: str,
        task: str,
        model: str,
        prompt: str,
        system: str | None,
        response: str,
        duration: float,
        usage: Any,
        extra_body: dict[str, Any] | None,
        sampling: dict[str, Any] | None,
    ) -> None:
        log_path = Path(os.getenv("SYNDATA_LLM_LOG", "")) if os.getenv("SYNDATA_LLM_LOG") else artifact_path(self._output_dir, "llm_calls")  # type: ignore[arg-type]
        row = {
            "created_at": now_iso(),
            "role": role,
            "task": str(task),
            "model": model,
            "duration_seconds": round(duration, 3),
            "system": system,
            "prompt": prompt,
            "response": response,
            "in_tokens": getattr(usage, "prompt_tokens", None),
            "out_tokens": getattr(usage, "completion_tokens", None),
            "sampling": sampling,
            "extra_body": extra_body,
        }
        await asyncio.to_thread(append_jsonl, log_path, row)


# OpenAI-compatible decoding params sent as top-level call kwargs; everything else rides extra_body.
KNOWN_PARAMS = ("temperature", "top_p", "max_tokens", "frequency_penalty", "presence_penalty", "stop", "seed")
SAMPLING_DEFAULTS = {"temperature": 0.7, "max_tokens": 32768}
# Connection/control keys on a model role are not decoding params and must not be sent as sampling kwargs.
_CONNECTION_KEYS = frozenset({"base_url", "api_key", "api_key_env", "model", "min_interval_seconds", "timeout_seconds", "extra_body"})


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


def _rate_limit_details(exc: Exception) -> tuple[float | None, str | None]:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code != 429:
        return None, None
    headers = getattr(response, "headers", {}) or {}
    retry_after = headers.get("retry-after") or headers.get("Retry-After")
    wait = float(retry_after) if retry_after else 2.0
    body = getattr(response, "text", None)
    return min(60.0, wait), body


def fake_complete(prompt: str, counter: int = 0) -> str:
    """Deterministic fake model used by tests and smoke examples."""
    if "Generate exactly one synthetic data point matching the meta-prompt" in prompt:
        return f"Once upon a time there was a cat with id {counter}. The cat had adventures."
    if '"factors"' in prompt:
        return '{"factors":[{"name":"topic","description":"Subject area"},{"name":"difficulty","description":"Difficulty level"}]}'
    if '"children"' in prompt and "Refine" not in prompt:
        return '{"children":[{"name":"alpha","description":"Alpha branch"},{"name":"beta","description":"Beta branch"}]}'
    if '"children"' in prompt and "Refine" in prompt:
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
