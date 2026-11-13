# Providers and models

`simula` talks to one OpenAI-compatible chat-completions endpoint, shared by all three model roles.
OpenRouter is the default; any endpoint that speaks the same API works.

## The provider and the key

```yaml
provider:
  base_url: "https://openrouter.ai/api/v1"
  api_key_env: "OPENROUTER_API_KEY"
  timeout_seconds: 180
```

The key is read **only** from a `.env` file in the directory you run `simula` from, under the name
`api_key_env` gives:

```bash
echo 'OPENROUTER_API_KEY=sk-or-...' > .env
```

A variable exported in your shell is deliberately ignored, so the key always comes from a file you can
see. Never put a key in the YAML, and keep `.env` out of version control. `simula validate` warns when a
real run has no key; the run itself fails on its first model call.

To use another provider, change both fields, for example `base_url: "https://api.openai.com/v1"` with
`api_key_env: "OPENAI_API_KEY"`. Model ids are whatever that provider calls its models.

## Model ids

Each role names its model under `models`:

```yaml
models:
  strategic: { model: "deepseek/deepseek-v4-pro" }
  bulk:      { model: "deepseek/deepseek-v4-flash", max_tokens: 65536 }
  critic:    { model: "deepseek/deepseek-v4-flash" }
```

See [Model roles](../concepts/model-roles.md) for what each role does and where to spend. `"fake"` runs a
role offline.

On OpenRouter the same model can be served by several providers at different prices. Suffixes such as
`:nitro` route for speed: in our pilots `:nitro` billed about 2.1× list price and reasoned about twice as
much per call. Use the plain id for large runs unless speed matters.

## Reasoning models

Models that think before answering (DeepSeek V4, OpenAI's o-series and others) spend hidden reasoning
tokens on every call. There is no automatic detection; configure it per role with `extra_body`, which is
passed to the provider as is:

```yaml
models:
  bulk:
    model: "deepseek/deepseek-v4-flash"
    max_tokens: 65536
    extra_body: {reasoning: {effort: low, exclude: true}}
```

`effort` sets how much the model reasons; `exclude` keeps the reasoning out of the reply. Reasoning still
counts toward `max_tokens` and is billed as output, so give batched roles room: a reply cut off at the
limit loses the whole batch.

## Decoding parameters

Decoding params resolve in three layers, last one wins:

```text
built-in defaults             {temperature: 0.7, max_tokens: 32768}
  └─ models.<role>            e.g. models.bulk.temperature: 0.9
       └─ sampling.tasks.<task>   e.g. sampling.tasks.generate.temperature: 1.1   ← wins
```

```yaml
sampling:
  tasks:
    generate:    {temperature: 1.1, top_p: 0.95, min_p: 0.05}
    repair:      {temperature: 0.0}
    meta_prompt: {temperature: 1.1}
```

Each task is handled by one role, so you name the task, not the role; the task names are listed in
[Configuration](../reference/configuration.md#sampling). Standard OpenAI params (`temperature`,
`top_p`, `max_tokens`, `frequency_penalty`, `presence_penalty`, `stop`, `seed`) are sent as normal call
arguments; anything else (`min_p`, `top_k`, `repetition_penalty`, …) goes in `extra_body`, so
provider-specific knobs work without lock-in. The params actually sent are recorded on every row of
`llm_calls.jsonl`.

The 32K default `max_tokens` is generous: a role without its own cap can return very long, expensive
replies. Set `models.<role>.max_tokens` when cost matters.

## Timeouts, retries and rate limits

- Every call has a timeout of `provider.timeout_seconds` (180 by default), so a hung connection fails
  instead of stalling a worker.
- Transient failures (timeouts, connection errors, 5xx, 408, 409, 429) are retried with exponential
  backoff, honouring `Retry-After`. Other errors, such as a bad key or a bad request, fail at once.
- An attempt whose calls fail for good is written as a rejected row with its reason; the run carries on.
- `generation.concurrency` caps calls in flight. If the provider rate-limits, lower it; there is no other
  pacing.

A `402 Insufficient credits` error while your key still has budget means the account behind the key is
empty; top it up rather than retrying.

## One provider for all roles

All three roles share one `provider`. Per-role endpoints or keys (a strong model on one provider, a cheap
one on another) are not supported yet; through OpenRouter, one key reaches most models anyway.
