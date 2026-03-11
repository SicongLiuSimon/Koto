import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union, Generator

logger = logging.getLogger(__name__)


class LLMProvider(ABC):
    """
    Abstract base class for LLM providers.
    Standardizes the interface for OpenAI, Google Gemini, Anthropic, Ollama, etc.
    """

    def _log_request(self, prompt: Union[str, List], model: str) -> None:
        """
        Log an outgoing LLM request at DEBUG level.
        Concrete subclasses should call this at the start of generate_content().
        """
        prompt_len = len(prompt) if isinstance(prompt, str) else len(prompt)
        logger.debug("[LLMProvider] generate_content model=%s prompt_len=%d class=%s",
                     model, prompt_len, type(self).__name__)

    def _log_response(
        self,
        model: str,
        response_len: int = 0,
        *,
        error: bool = False,
        error_msg: str = "",
    ) -> None:
        """
        Log the result of an LLM call.
        Call with error=True when generate_content raises or returns an error.
        """
        if error:
            logger.warning("[LLMProvider] generate_content ERROR model=%s class=%s: %s",
                           model, type(self).__name__, error_msg)
        else:
            logger.debug("[LLMProvider] generate_content OK model=%s response_len=%d class=%s",
                         model, response_len, type(self).__name__)
    
    @abstractmethod
    def generate_content(
        self, 
        prompt: Union[str, List[Dict[str, Any]]],
        model: str,
        system_instruction: Optional[str] = None,
        tools: Optional[List[Any]] = None,
        stream: bool = False,
        **kwargs
    ) -> Union[Dict[str, Any], Generator[Dict[str, Any], None, None]]:
        """
        Generate content from the LLM.
        
        Args:
            prompt: The user prompt or list of messages
            model: Model identifier
            system_instruction: System prompt
            tools: List of tool definitions
            stream: Whether to stream the response
            **kwargs: Additional provider-specific arguments (temperature, etc.)
            
        Returns:
            Structured response dictionary or generator if streaming
        """
        pass
        
    @abstractmethod
    def get_token_count(self, prompt: Union[str, List[Dict[str, Any]]], model: str) -> int:
        """Count tokens for a given prompt/model"""
        pass
