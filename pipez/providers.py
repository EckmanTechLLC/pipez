"""LLM providers: Anthropic (native API) and OpenAI-compatible (vLLM etc.).

A provider turns (system, prompt, schema, tier) into (parsed_json, usage).
Retry policy lives in the llm.py facade; providers raise ProviderTransientError
(or Anthropic's own transient exceptions) for failures worth retrying.
"""
import inspect
import json
import logging
import os
import re
import time

import httpx2 as httpx  # anthropic 1.13 depends on httpx2, not httpx
import jsonschema

from . import config

logger = logging.getLogger("pipez.providers")


class LLMError(Exception):
    pass


class ProviderTransientError(LLMError):
    """Transport error or HTTP 5xx from a provider — worth a slow retry."""


class ProviderTimeoutError(LLMError):
    """Local call exceeded its client timeout — retrying would stack identical runaways."""
    retryable = False


class ProviderSchemaError(LLMError):
    """Local output parsed but violates the call's schema — an identical retry is unlikely to differ."""
    retryable = False


class ProviderTruncatedError(LLMError):
    """Local call hit max_tokens — an identical retry would truncate again."""
    retryable = False


def is_transient(e: Exception) -> bool:
    """True if `e` should be retried by the facade's slow-retry loop."""
    if isinstance(e, ProviderTransientError):
        return True
    try:
        import anthropic
    except ImportError:
        return False
    if isinstance(e, (anthropic.APIConnectionError, anthropic.RateLimitError)):
        return True
    if isinstance(e, anthropic.APIStatusError):
        return e.status_code >= 500
    return False


class Provider:
    async def complete(self, system: str, prompt: str, schema: dict, tier: dict,
                       *, fetch: bool = False) -> tuple[dict, dict]:
        raise NotImplementedError


class AnthropicProvider(Provider):
    """Async Claude client making schema-constrained, search-capable calls."""

    MAX_PAUSE_CONTINUATIONS = 5

    def __init__(self):
        import anthropic
        self._anthropic = anthropic
        self._client = anthropic.AsyncAnthropic()
        self._fallbacks_ok = config.ENABLE_FALLBACKS

    def _tools(self, fetch=False):
        if not config.ENABLE_SEARCH:
            return None
        tools = [{"type": "web_search_20260209", "name": "web_search",
                  "max_uses": config.SEARCH_MAX_USES}]
        if fetch:
            tools.append({"type": "web_fetch_20260209", "name": "web_fetch",
                          "max_uses": config.SEARCH_MAX_USES * 2})
        return tools

    async def _create(self, **kwargs):
        # Streaming so large max_tokens doesn't hit HTTP timeouts.
        # Server-side refusal fallbacks apply to the Opus/Sonnet 5.5/Fable models only (never Haiku).
        model = kwargs.get("model", "")
        wants_fallbacks = model.startswith(
            ("claude-opus-5", "claude-sonnet-5-5", "claude-fable"))
        if self._fallbacks_ok and wants_fallbacks:
            try:
                async with self._client.beta.messages.stream(
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                    **kwargs,
                ) as stream:
                    return await stream.get_final_message()
            except self._anthropic.BadRequestError as e:
                # Fallbacks unavailable on this account/endpoint — drop them
                # for the rest of the process rather than failing every task.
                # Only when the 400 is about fallbacks; other errors propagate.
                text = f"{getattr(e, 'message', '')} {e}".lower()
                if "fallback" not in text:
                    raise
                logger.warning("fallbacks rejected, disabling: %s", e.message)
                self._fallbacks_ok = False
        async with self._client.messages.stream(**kwargs) as stream:
            return await stream.get_final_message()

    async def complete(self, system: str, prompt: str, schema: dict, tier: dict,
                       *, fetch: bool = False) -> tuple[dict, dict]:
        """Returns (parsed_json, usage {input_tokens, output_tokens, model})."""
        messages = [{"role": "user", "content": prompt}]
        kwargs = dict(
            model=tier["model"],
            max_tokens=config.MAX_TOKENS,
            system=system,
            output_config={
                "format": {"type": "json_schema", "schema": schema},
                "effort": tier["effort"],
            },
        )
        tools = self._tools(fetch)
        if tools:
            kwargs["tools"] = tools

        usage = {"input_tokens": 0, "output_tokens": 0, "model": kwargs["model"]}
        for _ in range(self.MAX_PAUSE_CONTINUATIONS + 1):
            response = await self._create(messages=messages, **kwargs)
            usage["input_tokens"] += response.usage.input_tokens
            usage["output_tokens"] += response.usage.output_tokens
            usage["model"] = response.model
            if response.stop_reason == "pause_turn":
                # Long server-tool turn paused; append and resume.
                messages = messages + [{"role": "assistant", "content": response.content}]
                continue
            break
        else:
            raise LLMError("turn still paused after %d continuations"
                           % self.MAX_PAUSE_CONTINUATIONS)

        if response.stop_reason == "refusal":
            detail = ""
            if response.stop_details:
                detail = f" ({response.stop_details.category}: {response.stop_details.explanation})"
            raise LLMError(f"model refused{detail}")
        if response.stop_reason == "max_tokens":
            raise LLMError("response truncated at max_tokens")

        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise LLMError("no text block in response")
        return json.loads(text), usage

    async def render_artifacts(self, system: str, prompt: str,
                               dest_dir: str, tier: dict) -> tuple[list, dict]:
        """Turn a finished deliverable into real files via the server-side
        code-execution sandbox; download them into dest_dir.
        Returns ([{filename, path, bytes}], usage)."""
        messages = [{"role": "user", "content": prompt}]
        kwargs = dict(
            model=tier["model"],  # rendering is formatting work
            max_tokens=config.MAX_TOKENS,
            system=system,
            tools=[{"type": "code_execution_20260120", "name": "code_execution"}],
        )
        usage = {"input_tokens": 0, "output_tokens": 0, "model": tier["model"]}
        file_ids = []
        for _ in range(self.MAX_PAUSE_CONTINUATIONS + 1):
            response = await self._create(messages=messages, **kwargs)
            usage["input_tokens"] += response.usage.input_tokens
            usage["output_tokens"] += response.usage.output_tokens
            usage["model"] = response.model
            for block in response.content:
                if block.type == "bash_code_execution_tool_result":
                    result = block.content
                    if getattr(result, "type", "") == "bash_code_execution_result":
                        for entry in (result.content or []):
                            if getattr(entry, "type", "") == "bash_code_execution_output":
                                file_ids.append(entry.file_id)
            if response.stop_reason == "pause_turn":
                messages = messages + [{"role": "assistant", "content": response.content}]
                continue
            break

        os.makedirs(dest_dir, exist_ok=True)
        files = []
        for file_id in file_ids:
            try:
                meta = await self._client.beta.files.retrieve_metadata(file_id)
                safe = os.path.basename(meta.filename or file_id)
                if not safe or safe in (".", ".."):
                    continue
                path = os.path.join(dest_dir, safe)
                content = await self._client.beta.files.download(file_id)
                written = content.write_to_file(path)
                if inspect.isawaitable(written):
                    await written
                files.append({"filename": safe, "path": path,
                              "bytes": os.path.getsize(path)})
            except Exception as e:
                logger.warning("artifact download failed for %s: %s", file_id, e)
        return files, usage


_THINK_RE = re.compile(r"^\s*<think>.*?</think>\s*", re.DOTALL)


class OpenAICompatProvider(Provider):
    """OpenAI-compatible chat completions (vLLM). Effort and web tools are
    ignored; JSON is enforced via response_format json_schema."""

    def __init__(self, transport: httpx.AsyncBaseTransport = None):
        self._transport = transport

    def _client(self, timeout) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=timeout)

    @staticmethod
    def _headers(tier: dict = None) -> dict:
        tier = tier or {}
        key = tier["api_key"] if "api_key" in tier else config.LOCAL_API_KEY
        if key:
            return {"Authorization": "Bearer %s" % key}
        return {}

    @staticmethod
    def _get(tier: dict, key: str, default):
        return tier[key] if key in tier else default

    async def complete(self, system: str, prompt: str, schema: dict, tier: dict,
                       *, fetch: bool = False) -> tuple[dict, dict]:
        base = (tier.get("base_url") or config.LOCAL_BASE_URL).rstrip("/")
        g = self._get
        reasoning = g(tier, "reasoning_effort", None)
        max_tokens = g(tier, "max_tokens", config.MAX_TOKENS)
        timeout = g(tier, "timeout", config.LOCAL_TIMEOUT)
        mode = g(tier, "structured", "json_schema")
        if mode not in ("json_schema", "json_object", "prompt"):
            mode = "json_schema"
        if mode != "json_schema":
            prompt = ("%s\n\n## Output format\nRespond with ONLY one JSON object, no prose "
                      "and no code fence, that validates against this JSON Schema:\n%s"
                      % (prompt, json.dumps(schema)))
        body = {
            "model": tier["model"],
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": g(tier, "temperature", config.LOCAL_TEMPERATURE),
            "chat_template_kwargs": {"enable_thinking": g(tier, "thinking", config.LOCAL_THINKING)},
        }
        if mode == "json_schema":
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "out", "schema": schema,
                                                       "strict": True}}
        elif mode == "json_object":
            body["response_format"] = {"type": "json_object"}
        if reasoning:
            body["reasoning_effort"] = reasoning
        try:
            started = time.monotonic()
            async with self._client(timeout) as client:
                resp = await client.post(base + "/v1/chat/completions",
                                         json=body, headers=self._headers(tier))
        except httpx.TimeoutException as e:
            raise ProviderTimeoutError(
                "local provider timed out after %ss (model %s, %s)"
                % (timeout, tier.get("model"), base))
        except httpx.HTTPError as e:
            raise ProviderTransientError("local provider transport error: %r" % e)
        if resp.status_code >= 500:
            raise ProviderTransientError(
                "local provider HTTP %d: %s" % (resp.status_code, resp.text))
        if resp.status_code >= 400:
            raise LLMError("local provider HTTP %d: %s" % (resp.status_code, resp.text))

        data = resp.json()
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise LLMError("malformed local provider response: %s" % resp.text[:500])
        if choice.get("finish_reason") == "length":
            elapsed = time.monotonic() - started
            out_tok = (data.get("usage") or {}).get("completion_tokens", 0)
            raise ProviderTruncatedError(
                "response truncated at max_tokens=%s (local: %.0fs, %s output tokens)"
                % (max_tokens, elapsed, out_tok))
        content = _THINK_RE.sub("", content, count=1)
        if mode != "json_schema":
            lo, hi = content.find("{"), content.rfind("}")
            if lo != -1 and hi > lo:
                content = content[lo:hi + 1]
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as e:
            raise LLMError("local provider returned invalid JSON: %s" % e)
        try:
            validator = jsonschema.validators.validator_for(
                schema, default=jsonschema.Draft7Validator)(schema)
            err = next(iter(validator.iter_errors(parsed)), None)
        except jsonschema.SchemaError as e:
            logger.warning("cannot validate local output, bad schema: %s", e.message)
            err = None
        if err is not None:
            logger.debug("local output failing schema: %s", content[:500])
            raise ProviderSchemaError(
                "local output violates schema at %s: %s (structured=%s, model %s)"
                % (err.json_path, err.message, mode, tier.get("model")))
        u = data.get("usage") or {}
        usage = {"input_tokens": u.get("prompt_tokens", 0),
                 "output_tokens": u.get("completion_tokens", 0),
                 "model": data.get("model") or tier["model"],
                 "structured": mode}
        return parsed, usage

    async def healthy(self, tier: dict = None) -> bool:
        tier = tier or {}
        base = tier.get("base_url") or config.LOCAL_BASE_URL
        if not base:
            return False
        try:
            async with self._client(5.0) as client:
                resp = await client.get(base.rstrip("/") + "/v1/models",
                                        headers=self._headers(tier))
            return resp.status_code == 200
        except httpx.HTTPError:
            return False
