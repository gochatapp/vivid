"""
LLM Provider abstraction layer.
Extracted from vivid.py PROVIDER ABSTRACTION LAYER section.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, AsyncIterator

# Provider implementations
from ollama import AsyncClient as OllamaAsyncClient

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None


# =============================================================================
# PROVIDER ABSTRACTION LAYER
# =============================================================================

class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    def __init__(self, name: str, config: Dict[str, Any]):
        self.name = name
        self.config = config
        self.description = config.get("description", name)
        self.show_on_provider_list = config.get("show_on_provider_list", True)

    @abstractmethod
    async def chat(self, model: str, messages: List[Dict[str, str]], options: Dict[str, Any]) -> str:
        """Send a chat completion request and return the response text."""
        pass

    @abstractmethod
    async def list_models(self) -> List[str]:
        """List available models. Returns list of model names."""
        pass

    @abstractmethod
    async def pull_model(self, model_name: str) -> AsyncIterator[Dict[str, Any]]:
        """Pull/download a model. Returns async iterator of progress dicts."""
        pass

    @abstractmethod
    async def delete_model(self, model_name: str) -> None:
        """Delete a model."""
        pass

    @abstractmethod
    def supports_model_management(self) -> bool:
        """Whether this provider supports model pull/delete operations."""
        pass

    def get_display_name(self) -> str:
        return self.name


class OllamaProvider(LLMProvider):
    """Ollama provider using the official ollama-python client."""

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(name, config)
        url = config.get("url")
        api_key = config.get("api_key")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        self.client = OllamaAsyncClient(host=url, headers=headers, timeout=None)

    async def chat(self, model: str, messages: List[Dict[str, str]], options: Dict[str, Any]) -> str:
        ollama_options = {**options}
        if "think" in ollama_options:
            think = ollama_options.pop("think")
            if think:
                ollama_options["think"] = True
        res = await self.client.chat(model=model, messages=messages, options=ollama_options)
        return res.message.content

    async def list_models(self) -> List[str]:
        resp = await self.client.list()
        models = getattr(resp, 'models', [])
        return [m.model for m in models]

    async def pull_model(self, model_name: str) -> AsyncIterator[Dict[str, Any]]:
        async for part in await self.client.pull(model=model_name, stream=True):
            yield part

    async def delete_model(self, model_name: str) -> None:
        await self.client.delete(model=model_name)

    def supports_model_management(self) -> bool:
        return True


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI-compatible provider (OpenRouter, llama.cpp --api-server, Together AI, etc.)."""

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(name, config)
        if AsyncOpenAI is None:
            raise ImportError("openai package not installed. Run: pip install openai")

        url = config.get("url")
        api_key = config.get("api_key", "not-needed")
        extra_headers = config.get("extra_headers", {})
        self.models_url = config.get("models_url")

        self.client = AsyncOpenAI(
            base_url=url,
            api_key=api_key,
            default_headers=extra_headers if extra_headers else None,
            timeout=None
        )
        self.model_aliases = config.get("model_aliases", {})

    def _resolve_model(self, model: str) -> str:
        return self.model_aliases.get(model, model)

    def _convert_options(self, options: Dict[str, Any]) -> Dict[str, Any]:
        openai_options = {}
        if "temperature" in options:
            openai_options["temperature"] = options["temperature"]
        if "top_p" in options:
            openai_options["top_p"] = options["top_p"]
        if "num_ctx" in options:
            openai_options["max_tokens"] = options["num_ctx"]
        if "repeat_penalty" in options:
            openai_options["frequency_penalty"] = options["repeat_penalty"] - 1.0
        return openai_options

    async def chat(self, model: str, messages: List[Dict[str, str]], options: Dict[str, Any]) -> str:
        model = self._resolve_model(model)
        openai_options = self._convert_options(options)

        if options.get("think"):
            openai_options["reasoning_effort"] = "high"

        response = await self.client.chat.completions.create(
            model=model,
            messages=messages,
            **openai_options
        )
        return response.choices[0].message.content or ""

    async def list_models(self) -> List[str]:
        try:
            models = await self.client.models.list()
            model_ids = [m.id for m in models.data]
            if model_ids:
                return model_ids
        except Exception:
            pass

        if self.models_url:
            try:
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    headers = {}
                    api_key = self.config.get("api_key")
                    if api_key and api_key != "not-needed":
                        headers["Authorization"] = f"Bearer {api_key}"
                    extra_headers = self.config.get("extra_headers", {})
                    headers.update(extra_headers)
                    async with session.get(self.models_url, headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            model_ids = [
                                model.get("id")
                                for model in data.get("data", [])
                                if model.get("id")
                            ]
                            if model_ids:
                                return model_ids
            except Exception:
                pass

        base_url = self.config.get("url", "")
        if base_url.endswith("/v1"):
            fallback_url = base_url[:-3] + "/models"
        else:
            fallback_url = base_url.rstrip("/") + "/models"

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                headers = {}
                api_key = self.config.get("api_key")
                if api_key and api_key != "not-needed":
                    headers["Authorization"] = f"Bearer {api_key}"
                extra_headers = self.config.get("extra_headers", {})
                headers.update(extra_headers)
                async with session.get(fallback_url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return [m.get("id") for m in data.get("data", []) if m.get("id")]
        except Exception:
            pass

        return self.config.get("models", [])

    async def pull_model(self, model_name: str) -> AsyncIterator[Dict[str, Any]]:
        yield {"status": "error", "error": "Model pulling not supported for OpenAI-compatible providers"}

    async def delete_model(self, model_name: str) -> None:
        raise NotImplementedError("Model deletion not supported for OpenAI-compatible providers")

    def supports_model_management(self) -> bool:
        return False


class GeminiProvider(LLMProvider):
    """Google Gemini provider."""

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(name, config)
        if genai is None or types is None:
            raise ImportError("google-genai package not installed. Run: pip install google-genai")

        api_key = config.get("api_key")
        if not api_key:
            raise ValueError("Gemini provider requires 'api_key' in config")

        self.client = genai.Client(api_key=api_key)
        self.model_name = config.get("model", "gemini-1.5-flash")

    async def chat(self, model: str, messages: List[Dict[str, str]], options: Dict[str, Any]) -> str:
        system_instruction = None
        contents = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                system_instruction = content
            elif role == "user":
                contents.append({"role": "user", "parts": [{"text": content}]})
            elif role == "assistant":
                contents.append({"role": "model", "parts": [{"text": content}]})

        config = types.GenerateContentConfig(
            temperature=options.get("temperature", 0.8),
            top_p=options.get("top_p", 0.9),
            max_output_tokens=options.get("num_ctx", 4096),
            system_instruction=system_instruction,
        )

        response = await self.client.aio.models.generate_content_async(
            model=model,
            contents=contents,
            config=config
        )
        return response.text or ""

    async def list_models(self) -> List[str]:
        try:
            models = []
            for m in self.client.models.list():
                if "generateContent" in m.supported_generation_methods:
                    models.append(m.name.replace("models/", ""))
            return models
        except Exception:
            return [self.model_name]

    async def pull_model(self, model_name: str) -> AsyncIterator[Dict[str, Any]]:
        yield {"status": "error", "error": "Model pulling not supported for Gemini"}

    async def delete_model(self, model_name: str) -> None:
        raise NotImplementedError("Model deletion not supported for Gemini")

    def supports_model_management(self) -> bool:
        return False


def create_provider(name: str, config: Dict[str, Any]) -> LLMProvider:
    """Factory function to create provider instances."""
    provider_type = config.get("type", "ollama")

    if provider_type == "ollama":
        return OllamaProvider(name, config)
    elif provider_type == "openai_compatible":
        return OpenAICompatibleProvider(name, config)
    elif provider_type == "gemini":
        return GeminiProvider(name, config)
    else:
        raise ValueError(f"Unknown provider type: {provider_type}")


# Initialize providers after all classes are defined
# This is called from config.initialize_providers()
def init_providers():
    """Initialize provider instances from config."""
    from .config import providers, provider_display_info, AI_PROVIDERS, ollama_clients, providers_config
    
    providers.clear()
    provider_display_info.clear()
    AI_PROVIDERS.clear()
    
    for cfg in providers_config:
        name = cfg.get("name")
        if not name:
            continue
        try:
            provider = create_provider(name, cfg)
            providers[name] = provider
            if provider.show_on_provider_list:
                AI_PROVIDERS.append(name)
                provider_display_info[name] = provider.get_display_name()
        except Exception as e:
            print(f"⚠️ Failed to initialize provider '{name}': {e}")
    
    # Backward compatibility
    ollama_clients.clear()
    for name, provider in providers.items():
        if isinstance(provider, OllamaProvider):
            ollama_clients[name] = provider.client