"""Thin wrapper around the local Ollama HTTP API.

Every call goes to ``localhost`` — this is the component that makes the privacy
guarantee real (PROJECT_PLAN.md §16). No email text is ever sent anywhere else.

Handles the practical failure modes of CPU inference: the server not running,
the model not pulled, slow first-token latency, and occasional timeouts.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

from src import config

log = logging.getLogger(__name__)


class OllamaError(RuntimeError):
    """Base class for Ollama failures."""


class OllamaUnavailableError(OllamaError):
    """The Ollama server could not be reached."""


class OllamaTimeoutError(OllamaError):
    """The model did not respond within the configured timeout."""


class OllamaClient:
    """Minimal client for the ``/api/generate`` endpoint."""

    def __init__(
        self,
        host: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
        max_retries: int = 2,
    ) -> None:
        self.host = (host or config.OLLAMA_HOST).rstrip("/")
        self.model = model or config.OLLAMA_MODEL
        self.timeout = timeout or config.OLLAMA_TIMEOUT
        self.max_retries = max_retries
        # Reusing one connection avoids per-call TCP setup during a batch.
        self._session = requests.Session()
        # Whether the server accepted a JSON-schema `format`; falls back to
        # plain JSON mode if not (older builds only support "json").
        self._schema_supported = True

    # --- Health ---------------------------------------------------------

    def is_available(self) -> bool:
        """True if the Ollama server answers."""
        try:
            response = self._session.get(f"{self.host}/api/tags", timeout=5)
            response.raise_for_status()
            return True
        except requests.RequestException:
            return False

    def list_models(self) -> list[str]:
        """Return the names of locally installed models."""
        try:
            response = self._session.get(f"{self.host}/api/tags", timeout=10)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise OllamaUnavailableError(
                f"Could not reach Ollama at {self.host}. Is it running? ({exc})"
            ) from exc
        return [m.get("name", "") for m in payload.get("models", [])]

    def ensure_ready(self) -> None:
        """Raise a clear, actionable error unless the server and model are ready."""
        if not self.is_available():
            raise OllamaUnavailableError(
                f"Ollama is not reachable at {self.host}.\n"
                "Start it with:  ollama serve"
            )
        models = self.list_models()
        # Ollama reports "llama3.2:latest"; accept a bare "llama3.2" too.
        base = self.model.split(":")[0]
        if not any(m == self.model or m.split(":")[0] == base for m in models):
            available = ", ".join(models) or "none"
            raise OllamaError(
                f"Model '{self.model}' is not installed (available: {available}).\n"
                f"Pull it with:  ollama pull {self.model}"
            )

    # --- Generation -----------------------------------------------------

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
        num_predict: int = 1024,
    ) -> str:
        """Run one completion and return the raw response text.

        ``schema`` requests structured output; if the server rejects it, the
        client transparently falls back to plain JSON mode for the rest of its
        life. Retries on transient network/timeout errors.
        """
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            payload: dict[str, Any] = {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": temperature, "num_predict": num_predict},
                # Keep the model resident so later emails in a batch are fast.
                "keep_alive": "5m",
            }
            if system:
                payload["system"] = system
            if schema is not None and self._schema_supported:
                payload["format"] = schema
            else:
                payload["format"] = "json"

            try:
                response = self._session.post(
                    f"{self.host}/api/generate", json=payload, timeout=self.timeout
                )
            except requests.Timeout as exc:
                last_error = exc
                log.warning(
                    "Ollama timed out after %ss (attempt %d/%d)",
                    self.timeout, attempt, self.max_retries,
                )
                continue
            except requests.RequestException as exc:
                last_error = exc
                log.warning(
                    "Ollama request failed (attempt %d/%d): %s",
                    attempt, self.max_retries, exc,
                )
                time.sleep(min(2 ** attempt, 5))
                continue

            if response.status_code == 400 and self._schema_supported and schema:
                # This build likely cannot compile a JSON schema into a grammar.
                log.warning(
                    "Ollama rejected the JSON schema; falling back to plain JSON mode."
                )
                self._schema_supported = False
                continue

            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                raise OllamaError(
                    f"Ollama returned {response.status_code}: {response.text[:300]}"
                ) from exc

            data = response.json()
            if data.get("error"):
                raise OllamaError(f"Ollama error: {data['error']}")
            return data.get("response", "")

        if isinstance(last_error, requests.Timeout):
            raise OllamaTimeoutError(
                f"Ollama did not respond within {self.timeout}s. "
                "CPU inference can be slow; raise OLLAMA_TIMEOUT in .env if needed."
            ) from last_error
        raise OllamaUnavailableError(
            f"Could not reach Ollama at {self.host} after {self.max_retries} "
            f"attempts: {last_error}"
        ) from last_error


def _main() -> int:
    """Quick connectivity check:  python -m src.extraction.ollama_client"""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    client = OllamaClient()
    print(f"Host:  {client.host}")
    print(f"Model: {client.model}")
    try:
        client.ensure_ready()
    except OllamaError as exc:
        print(f"\nNot ready: {exc}")
        return 1
    print(f"Installed models: {', '.join(client.list_models())}")

    started = time.time()
    reply = client.generate(
        prompt='Return this JSON exactly: {"ok": true}',
        schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        },
    )
    print(f"Test generation ({time.time() - started:.1f}s): {reply.strip()}")
    print("\nOllama is ready for extraction.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
