"""Local Ollama client management for the autonomous agent."""

import os
import time
import threading
import subprocess
import shutil
import requests

class OllamaLLM:
    def __init__(self, model="ministral-3b"):
        self.model = model
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        self.url = f"{base_url}/api/generate"
        self.health_url = f"{base_url}/api/tags"
        self.session = requests.Session()
        self.connect_timeout = float(os.environ.get("OLLAMA_CONNECT_TIMEOUT", "1.5"))
        self.read_timeout = float(os.environ.get("OLLAMA_READ_TIMEOUT", "20"))
        self.health_timeout = float(os.environ.get("OLLAMA_HEALTH_TIMEOUT", "1.0"))
        self.failure_cooldown_seconds = float(os.environ.get("OLLAMA_FAILURE_COOLDOWN", "20"))
        self.circuit_breaker_threshold = int(os.environ.get("OLLAMA_CIRCUIT_BREAKER_THRESHOLD", "5"))
        self._failure_count = 0
        self._circuit_open = False
        self.startup_wait_timeout = float(os.environ.get("OLLAMA_START_TIMEOUT", "45"))
        self.wait_retry_interval = float(os.environ.get("OLLAMA_WAIT_RETRY_INTERVAL", "1.5"))
        self.startup_probe_timeout = float(os.environ.get("OLLAMA_STARTUP_PROBE_TIMEOUT", "20"))
        self.auto_start = os.environ.get("OLLAMA_AUTO_START", "1").strip().lower() in {"1", "true", "yes", "on"}
        self.log_unavailable = os.environ.get("OLLAMA_LOG_UNAVAILABLE", "0").strip().lower() in {"1", "true", "yes", "on"}
        self._unavailable_until = 0.0
        self._state_lock = threading.Lock()
        self._last_start_attempt = 0.0

    def _emit_log(self, message: str, log_fn=None, level: str = "SYSTEM") -> None:
        if not log_fn:
            return
        try:
            log_fn(message, level)
        except TypeError:
            log_fn(message)

    def _wake_service(self, log_fn=None) -> bool:
        if not self.auto_start:
            return False

        with self._state_lock:
            now = time.monotonic()
            if now - self._last_start_attempt < 5.0:
                return False
            self._last_start_attempt = now

        ollama_path = shutil.which("ollama")
        if not ollama_path:
            self._emit_log("Ollama CLI not found while attempting to wake the LLM service.", log_fn, "WARNING")
            return False

        self._emit_log("🧠 Waking Ollama service...", log_fn, "SYSTEM")
        try:
            subprocess.run(
                [ollama_path, "list"],
                capture_output=True,
                text=True,
                timeout=self.startup_probe_timeout,
            )
            return True
        except Exception as error:
            self._emit_log(f"Failed to wake Ollama service: {error}", log_fn, "WARNING")
            return False

    def _is_in_cooldown(self) -> bool:
        return time.monotonic() < self._unavailable_until

    def _mark_available(self):
        with self._state_lock:
            self._unavailable_until = 0.0
            self._failure_count = 0
            self._circuit_open = False

    def _mark_unavailable(self, error):
        with self._state_lock:
            cooldown_until = time.monotonic() + self.failure_cooldown_seconds
            should_log = time.monotonic() >= self._unavailable_until
            self._unavailable_until = max(self._unavailable_until, cooldown_until)
            self._failure_count += 1
            if self._failure_count >= self.circuit_breaker_threshold:
                self._circuit_open = True
                should_log = True  # Always log circuit breaker activation
        if should_log and self.log_unavailable:
            print(
                "DEBUG: Ollama unavailable; entering cooldown "
                f"for {self.failure_cooldown_seconds:.0f}s (failures: {self._failure_count}/{self.circuit_breaker_threshold}): {error}"
            )

    def is_circuit_open(self) -> bool:
        """Return True if the circuit breaker has tripped (too many consecutive failures)."""
        if not self._circuit_open:
            return False
        # Auto-reset after cooldown expires
        if not self._is_in_cooldown():
            with self._state_lock:
                self._circuit_open = False
                self._failure_count = 0
            return False
        return True

    def is_available(self, force_probe: bool = False) -> bool:
        if not force_probe and self._is_in_cooldown():
            return False
        try:
            response = self.session.get(self.health_url, timeout=self.health_timeout)
            response.raise_for_status()
            self._mark_available()
            return True
        except Exception as error:
            self._mark_unavailable(error)
            return False

    def wait_until_available(self, timeout_seconds=None, retry_interval=None, log_fn=None) -> bool:
        timeout = self.startup_wait_timeout if timeout_seconds is None else max(0.0, float(timeout_seconds))
        interval = self.wait_retry_interval if retry_interval is None else max(0.2, float(retry_interval))
        deadline = time.monotonic() + timeout
        announced_wait = False

        while time.monotonic() <= deadline:
            if self.is_available(force_probe=True):
                if announced_wait:
                    self._emit_log("✅ Ollama LLM is available.", log_fn, "SUCCESS")
                return True
            if not announced_wait:
                self._emit_log("⏳ Waiting for Ollama LLM to become available...", log_fn, "SYSTEM")
                announced_wait = True
            self._wake_service(log_fn=log_fn)
            time.sleep(interval)

        return self.is_available(force_probe=True)

    def _call_api(self, prompt, max_tokens=500, response_format: str = ""):
        """Send prompt to Ollama and return raw response text."""
        if self._is_in_cooldown() or self.is_circuit_open():
            return ""

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        if response_format == "json":
            payload["format"] = "json"
        try:
            response = self.session.post(
                self.url,
                json=payload,
                timeout=(self.connect_timeout, self.read_timeout),
            )
            response.raise_for_status()
            data = response.json()
            self._mark_available()
            return data.get("response", "")
        except Exception as e:
            self._mark_unavailable(e)
            return ""

    def generate(self, prompt, max_tokens=500, format: str = ""):
        """Return a plain-text response from Ollama for agent-driven tasks."""
        return self._call_api(prompt, max_tokens=max_tokens, response_format=format)

    def __call__(self, prompt, **kwargs):
        """Callable interface — returns the raw text string directly."""
        max_tokens = kwargs.get("max_tokens", 500)
        fmt = kwargs.get("format", "")
        return self._call_api(prompt, max_tokens=max_tokens, response_format=fmt)

_LLM_INSTANCE = None


def wait_for_llm_availability(timeout_seconds: float = 45.0, retry_interval: float = 1.5, log_fn=None) -> bool:
    global _LLM_INSTANCE
    if _LLM_INSTANCE is None:
        _LLM_INSTANCE = OllamaLLM()
    return _LLM_INSTANCE.wait_until_available(
        timeout_seconds=timeout_seconds,
        retry_interval=retry_interval,
        log_fn=log_fn,
    )

def get_llm_instance(required: bool = False, timeout_seconds: float = 0.0, retry_interval: float = 1.0, log_fn=None):
    """
    Lazy-load the Ollama client for content-aware field filling.
    """
    global _LLM_INSTANCE
    if _LLM_INSTANCE is None:
        _LLM_INSTANCE = OllamaLLM()

    if required:
        wait_timeout = timeout_seconds if timeout_seconds and timeout_seconds > 0 else _LLM_INSTANCE.startup_wait_timeout
        if not _LLM_INSTANCE.wait_until_available(
            timeout_seconds=wait_timeout,
            retry_interval=retry_interval,
            log_fn=log_fn,
        ):
            return None
        return _LLM_INSTANCE

    if not _LLM_INSTANCE.is_available():
        return None
    return _LLM_INSTANCE

