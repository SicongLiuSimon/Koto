import logging
import os
import time
import logging
from .base import LLMProvider

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None

logger = logging.getLogger(__name__)

# Interactions-only models: cannot use client.models.generate_content()
# Must be routed through rc.interactions.create(agent=...) instead.
# NOTE: gemini-3-flash-preview and gemini-3-pro-preview are regular models
# (use generate_content). Only deep-research-* are actual Interactions API agents.
_INTERACTIONS_ONLY_MODELS: frozenset = frozenset({
    "deep-research-pro-preview-12-2025",  # Research agent: Interactions API only
})
# No background=True restriction needed for current interactions models
_NO_BACKGROUND_MODELS: frozenset = frozenset()


class GeminiProvider(LLMProvider):
    """Google Gemini specific implementation of LLMProvider (google.genai SDK)"""

    MAX_RETRIES = 3
    RETRY_BASE_DELAY = 2.0  # seconds
    RETRYABLE_STATUS_CODES = {429, 503}

    def __init__(self, api_key: str = None):
        self.api_key = (
            api_key
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("API_KEY")
            or os.getenv("GOOGLE_API_KEY")
        )
        self.client = None

        if not genai or not types:
            logger.warning("google.genai package not installed")
            return

        if self.api_key:
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception as exc:
                logger.error(f"Failed to initialize google.genai client: {exc}")
                self.client = None
        else:
            logger.warning("No Google API KEY provided")

    def generate_content(
        self,
        prompt: Union[str, List[Dict[str, Any]]],
        model: str = "gemini-3-flash-preview",
        system_instruction: Optional[str] = None,
        tools: Optional[List[Any]] = None,
        stream: bool = False,
        **kwargs,
    ) -> Union[Dict[str, Any], Generator[Dict[str, Any], None, None]]:
        if not self.client or not types:
            raise ImportError("google.genai client not initialized")

        # Route interactions-only models through Interactions API transparently
        if model in _INTERACTIONS_ONLY_MODELS:
            return self._call_via_interactions_api(
                model, prompt, sys_instruction=system_instruction, stream=stream
            )

        try:
            config = types.GenerateContentConfig(
                temperature=kwargs.get("temperature", 0.7),
                top_p=kwargs.get("top_p", 0.95),
                top_k=kwargs.get("top_k", 64),
                max_output_tokens=kwargs.get("max_tokens", 8192),
                response_mime_type=kwargs.get("response_mime_type", "text/plain"),
                system_instruction=system_instruction,
                tools=self._format_tools(tools),
            )

            contents = self._format_prompt(prompt)

            if stream:
                response_iter = self.client.models.generate_content_stream(
                    model=model,
                    contents=contents,
                    config=config,
                )
                return self._stream_generator(response_iter)

            # Non-streaming with retry for transient errors
            return self._call_with_retry(model, contents, config)

        except Exception as exc:
            logger.error(f"Gemini generation error: {exc}")
            raise

    def _call_with_retry(self, model: str, contents, config):
        """Call generate_content with exponential backoff retry on 429/503."""
        last_exc = None
        for attempt in range(self.MAX_RETRIES):
            try:
                response = self.client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )
                return self._format_response(response)
            except Exception as exc:
                last_exc = exc
                # Check if retryable
                status_code = getattr(exc, "status_code", None)
                exc_str = str(exc)
                is_retryable = (
                    (status_code and status_code in self.RETRYABLE_STATUS_CODES)
                    or "429" in exc_str
                    or "503" in exc_str
                    or "RESOURCE_EXHAUSTED" in exc_str
                )
                if not is_retryable or attempt == self.MAX_RETRIES - 1:
                    raise
                delay = self.RETRY_BASE_DELAY * (2**attempt)
                logger.warning(
                    f"Retryable error (attempt {attempt + 1}/{self.MAX_RETRIES}), "
                    f"retrying in {delay:.1f}s: {exc}"
                )
                time.sleep(delay)

    def get_token_count(
        self, prompt: Union[str, List[Dict[str, Any]]], model: str
    ) -> int:
        if not self.client:
            return 0
        try:
            # google.genai currently doesn't guarantee count_tokens across all endpoints;
            # keep safe fallback.
            return 0
        except Exception:
            return 0

    def _format_tools(self, tools: Optional[List[Any]]) -> Optional[List[Any]]:
        if not tools or not types:
            return None

        formatted_tools: List[Any] = []
        function_declarations: List[Any] = []

        for tool in tools:
            if isinstance(tool, dict) and tool.get("name"):
                function_declarations.append(
                    types.FunctionDeclaration(
                        name=tool.get("name"),
                        description=tool.get("description") or "",
                        parameters_json_schema=self._normalize_schema(
                            tool.get("parameters") or {}
                        ),
                    )
                )
            elif isinstance(tool, types.Tool):
                formatted_tools.append(tool)

        if function_declarations:
            formatted_tools.append(
                types.Tool(function_declarations=function_declarations)
            )

        return formatted_tools or None

    def _normalize_schema(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize schema type values to JSON schema style expected by v2 SDK."""
        if not isinstance(schema, dict):
            return {"type": "object", "properties": {}, "required": []}

        def _walk(node: Any) -> Any:
            if isinstance(node, dict):
                out = {}
                for key, value in node.items():
                    if key == "type" and isinstance(value, str):
                        out[key] = value.lower()
                    else:
                        out[key] = _walk(value)
                return out
            if isinstance(node, list):
                return [_walk(item) for item in node]
            return node

        normalized = _walk(schema)
        if "type" not in normalized:
            normalized["type"] = "object"
        if "properties" not in normalized:
            normalized["properties"] = {}
        if "required" not in normalized:
            normalized["required"] = []
        return normalized

    def _format_prompt(self, prompt: Union[str, List[Dict[str, Any]]]):
        """Convert standard message format to google.genai content format."""
        if isinstance(prompt, str):
            return prompt

        contents: List[Dict[str, Any]] = []
        for msg in prompt:
            role = msg.get("role", "user")
            if role == "assistant":
                role = "model"
            elif role == "function":
                role = "tool"

            parts: List[Dict[str, Any]] = []

            text = msg.get("content")
            if text:
                parts.append({"text": str(text)})

            for tool_call in msg.get("tool_calls", []) or []:
                parts.append(
                    {
                        "function_call": {
                            "name": tool_call.get("name"),
                            "args": tool_call.get("args", {}),
                        }
                    }
                )

            if msg.get("role") == "function":
                parts.append(
                    {
                        "function_response": {
                            "name": msg.get("name", "unknown_tool"),
                            "response": {"content": msg.get("content", "")},
                        }
                    }
                )

            if not parts and msg.get("parts"):
                parts = msg["parts"]

            if parts:
                contents.append({"role": role, "parts": parts})

        return contents

    def _format_response(self, response: Any) -> Dict[str, Any]:
        """Convert google.genai response to standard dict format."""
        text = getattr(response, "text", "") or ""

        function_calls: List[Dict[str, Any]] = []
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            parts = getattr(candidates[0].content, "parts", None) or []
            for part in parts:
                function_call = getattr(part, "function_call", None)
                if function_call:
                    function_calls.append(
                        {
                            "name": function_call.name,
                            "args": dict(function_call.args or {}),
                        }
                    )

        usage_metadata = getattr(response, "usage_metadata", None)
        usage = {}
        if usage_metadata:
            usage = {
                "prompt_tokens": getattr(usage_metadata, "prompt_token_count", 0),
                "completion_tokens": getattr(
                    usage_metadata, "candidates_token_count", 0
                ),
            }

        return {
            "content": text,
            "tool_calls": function_calls,
            "usage": usage,
        }

    def _stream_generator(self, response_iterator: Any):
        """Yield standardized chunks from google.genai stream."""
        for chunk in response_iterator:
            text = getattr(chunk, "text", "") or ""
            finish_reason = None
            candidates = getattr(chunk, "candidates", None) or []
            if candidates:
                finish_reason = getattr(candidates[0], "finish_reason", None)

            yield {
                "content": text,
                "finish_reason": finish_reason,
            }

    def _call_via_interactions_api(
        self,
        model_id: str,
        prompt,
        sys_instruction: str = None,
        stream: bool = False,
        timeout: float = 90.0,
    ):
        """Route interactions-only models (e.g. deep-research-pro-preview) via rc.interactions.create().

        Returns the same dict format as generate_content() so all callers work unchanged.
        For stream=True, yields the full response as a single chunk (Interactions API has
        no token-level streaming).
        """
        flat = self._flatten_prompt_to_text(prompt)
        full_input = flat
        if sys_instruction:
            full_input = f"[\u7cfb\u7edf\u6307\u4ee4]\n{sys_instruction}\n\n[\u7528\u6237\u8f93\u5165]\n{flat}"

        # Build a client with extended timeout (interactions can take up to 5 min)
        try:
            import httpx
            from google.genai._api_client import HttpOptions as _HttpOptions
            http_client = httpx.Client(
                timeout=httpx.Timeout(300.0, connect=30.0), verify=True
            )
            rc = genai.Client(
                api_key=self.api_key,
                http_options=_HttpOptions(api_version="v1beta", httpx_client=http_client),
            )
        except Exception:
            rc = self.client

        background = model_id not in _NO_BACKGROUND_MODELS
        interaction = rc.interactions.create(
            agent=model_id,
            input=full_input[:80000],
            background=background,
            stream=False,
        )

        interaction_id = getattr(interaction, "id", None)
        status = getattr(interaction, "status", "")
        start_wait = time.time()

        while (
            interaction_id
            and status not in ("completed", "failed", "cancelled")
            and (time.time() - start_wait) < timeout
        ):
            time.sleep(2)
            interaction = rc.interactions.get(interaction_id)
            status = getattr(interaction, "status", "")

        if status not in ("completed", "failed", "cancelled"):
            try:
                rc.interactions.cancel(interaction_id)
            except Exception:
                pass
            raise TimeoutError(
                f"Interactions API timeout ({timeout}s) model={model_id}"
            )

        text = (
            self._get_interactions_text(getattr(interaction, "outputs", None))
            or self._get_interactions_text(interaction)
        ).strip()

        if stream:
            # Interactions API has no token-level streaming; emit as a single chunk
            def _single_chunk():
                yield {"content": text, "finish_reason": "stop"}
            return _single_chunk()

        return {"content": text, "tool_calls": [], "usage": {}}

    @staticmethod
    def _get_interactions_text(obj) -> str:
        """Recursively extract text from an Interactions API response object."""
        if obj is None:
            return ""
        if isinstance(obj, str):
            return obj
        if hasattr(obj, "text") and obj.text:
            return str(obj.text)
        if hasattr(obj, "parts"):
            return " ".join(
                str(p.text)
                for p in (obj.parts or [])
                if hasattr(p, "text") and p.text
            )
        if hasattr(obj, "outputs"):
            texts = [
                GeminiProvider._get_interactions_text(o)
                for o in (obj.outputs or [])
            ]
            return "\n".join(t for t in texts if t)
        return ""

    @staticmethod
    def _flatten_prompt_to_text(prompt) -> str:
        """Flatten a str or message-list prompt to plain text for Interactions API."""
        if isinstance(prompt, str):
            return prompt
        if not isinstance(prompt, list):
            return str(prompt)
        lines = []
        for msg in prompt:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if content:
                label = "\u52a9\u624b" if role in ("assistant", "model") else "\u7528\u6237"
                lines.append(f"{label}: {content}")
        return "\n".join(lines)
