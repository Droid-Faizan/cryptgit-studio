"""Local Ollama HTTP provider."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .telemetry import stage


class OllamaError(RuntimeError):
    """Base error for local Ollama failures."""


class OllamaUnavailableError(OllamaError):
    """Ollama cannot be reached."""


class OllamaModelError(OllamaError):
    """The configured model is unavailable."""


class OllamaResponseError(OllamaError):
    """Ollama returned an invalid response."""


class OllamaProvider:
    """Call Ollama's local JSON generation endpoint."""

    def __init__(
        self,
        endpoint: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.endpoint = (
            endpoint or os.environ.get("OLLAMA_HOST") or "http://localhost:11434"
        ).rstrip("/")
        self.model = model or os.environ.get("OLLAMA_MODEL") or "llama3.2"
        self.timeout = timeout

    def generate(self, prompt: str) -> str:
        """Return Ollama's generated JSON text."""
        return self._generate(prompt, "json")

    def generate_structured(self, prompt: str, schema: dict[str, object]) -> str:
        """Return JSON constrained by an Ollama response schema."""
        return self._generate(prompt, schema)

    def _generate(self, prompt: str, response_format: object) -> str:
        payload = json.dumps(
            {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "format": response_format,
            }
        ).encode("utf-8")
        request = Request(
            f"{self.endpoint}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with stage("cryptgit.ollama_inference", {"model": self.model}):
                with urlopen(request, timeout=self.timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            if error.code == 404:
                raise OllamaModelError(
                    f"Ollama model '{self.model}' was not found at {self.endpoint}; "
                    f"run 'ollama pull {self.model}'"
                ) from error
            raise OllamaUnavailableError(
                f"Ollama returned HTTP {error.code}"
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise OllamaUnavailableError(
                f"Unable to reach Ollama at {self.endpoint}: {error}"
            ) from error
        except json.JSONDecodeError as error:
            raise OllamaResponseError("Ollama returned invalid JSON") from error
        response_text = body.get("response")
        if not isinstance(response_text, str) or not response_text.strip():
            raise OllamaResponseError("Ollama response did not contain generated text")
        return response_text
