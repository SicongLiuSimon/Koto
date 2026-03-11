from typing import Any, Dict, List, Optional, Union, Generator
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


class GeminiProvider(LLMProvider):
    """Google Gemini specific implementation of LLMProvider (google.genai SDK)"""

    MAX_RETRIES = 3
    RETRY_BASE_DELAY = 2.0   # seconds
    RETRYABLE_STATUS_CODES = {429, 503}

    # These models only support the Interactions API and cannot use generate_content().
    # Any direct generate_content call with these models will receive a 400 INVALID_ARGUMENT.
    INTERACTIONS_ONLY_MODELS: frozenset = frozenset({
        "gemini-3-flash-preview",
        "gemini-3-pro-preview",
    })
    # Fallback model used whenever an Interactions-only model is passed to generate_content
    INTERACTIONS_FALLBACK_MODEL: str = "gemini-2.5-flash"

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

        # Interactions-only models cannot use generate_content(); substitute fallback model
        if model in self.INTERACTIONS_ONLY_MODELS:
            logger.warning(
                "[GeminiProvider] model '%s' only supports Interactions API; "
                "substituting '%s' for generate_content call",
                model, self.INTERACTIONS_FALLBACK_MODEL,
            )
            model = self.INTERACTIONS_FALLBACK_MODEL

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
                exc_str = str(exc)
                # If model was somehow still Interactions-only, fall back immediately (no retry)
                if "Interactions API" in exc_str and model in self.INTERACTIONS_ONLY_MODELS:
                    logger.warning(
                        "[GeminiProvider] Caught Interactions-API-only error for '%s'; "
                        "retrying once with '%s'",
                        model, self.INTERACTIONS_FALLBACK_MODEL,
                    )
                    model = self.INTERACTIONS_FALLBACK_MODEL
                    continue
                # Check if retryable
                status_code = getattr(exc, "status_code", None)
                is_retryable = (
                    (status_code and status_code in self.RETRYABLE_STATUS_CODES)
                    or "429" in exc_str
                    or "503" in exc_str
                    or "RESOURCE_EXHAUSTED" in exc_str
                )
                if not is_retryable or attempt == self.MAX_RETRIES - 1:
                    raise
                delay = self.RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    f"Retryable error (attempt {attempt + 1}/{self.MAX_RETRIES}), "
                    f"retrying in {delay:.1f}s: {exc}"
                )
                time.sleep(delay)

    def get_token_count(self, prompt: Union[str, List[Dict[str, Any]]], model: str) -> int:
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
                        parameters_json_schema=self._normalize_schema(tool.get("parameters") or {}),
                    )
                )
            elif isinstance(tool, types.Tool):
                formatted_tools.append(tool)

        if function_declarations:
            formatted_tools.append(types.Tool(function_declarations=function_declarations))

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
                "completion_tokens": getattr(usage_metadata, "candidates_token_count", 0),
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
