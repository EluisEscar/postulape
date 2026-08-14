"""
Cliente LLM unificado. Un solo .chat(system, user) que funciona con:
    - Groq   (OpenAI-compatible, tier gratuito con RPD alto)
    - Gemini (REST, tier gratuito)

Incluye reintentos con espera creciente (backoff) ante 429/5xx/timeout,
y parseo de JSON robusto (extrae el primer objeto {...} balanceado).
"""

import json
import re
import time
import httpx

from postulape import config

# Reintentos ante límites/errores transitorios.
_MAX_REINTENTOS = 4
_ESPERA_BASE = 5   # segundos: 5, 10, 20...


class LLMClient:
    def __init__(self, provider=None, model=None):
        self.provider = provider or config.LLM_PROVIDER
        self.model = model or config.LLM_MODEL
        self.timeout = 120

    # -- API pública -------------------------------------------------------
    def chat(self, system: str, user: str, temperature: float = 0.3) -> str:
        if self.provider == "groq":
            texto = self._groq(system, user, temperature)
        elif self.provider == "gemini":
            texto = self._gemini(system, user, temperature)
        else:
            raise ValueError(
                f"Proveedor LLM desconocido: {self.provider}. Usa 'groq' o 'gemini'."
            )
        return self._limpiar_think(texto)

    def chat_json(self, system: str, user: str, temperature: float = 0.2) -> dict:
        """Pide JSON y lo parsea de forma defensiva."""
        system = system + ("\n\nResponde ÚNICAMENTE con un objeto JSON válido, "
                           "sin markdown, sin ```json, sin texto adicional.")
        raw = self.chat(system, user, temperature)
        return self._parsear_json(raw)

    # -- POST con reintentos (backoff) ------------------------------------
    def _post(self, url, payload, headers=None):
        espera = _ESPERA_BASE
        for intento in range(_MAX_REINTENTOS):
            try:
                r = httpx.post(url, json=payload, headers=headers, timeout=self.timeout)
            except (httpx.TimeoutException, httpx.TransportError):
                if intento < _MAX_REINTENTOS - 1:
                    print(f"[LLM] red/timeout: reintento en {espera}s ({intento + 1})...")
                    time.sleep(espera); espera *= 2
                    continue
                raise
            if r.status_code in (429, 500, 502, 503, 504):
                if intento < _MAX_REINTENTOS - 1:
                    print(f"[LLM] {r.status_code}: esperando {espera}s y reintentando "
                          f"({intento + 1}/{_MAX_REINTENTOS - 1})...")
                    time.sleep(espera); espera *= 2
                    continue
            r.raise_for_status()
            return r
        r.raise_for_status()
        return r

    # -- Proveedores -------------------------------------------------------
    def _groq(self, system, user, temperature):
        if not config.GROQ_API_KEY:
            raise RuntimeError("Falta GROQ_API_KEY (variable de entorno).")
        r = self._post(
            "https://api.groq.com/openai/v1/chat/completions",
            {"model": self.model, "temperature": temperature,
             "messages": [{"role": "system", "content": system},
                          {"role": "user", "content": user}]},
            headers={"Authorization": f"Bearer {config.GROQ_API_KEY}",
                     "Content-Type": "application/json"},
        )
        return r.json()["choices"][0]["message"]["content"]

    def _gemini(self, system, user, temperature):
        if not config.GEMINI_API_KEY:
            raise RuntimeError("Falta GEMINI_API_KEY (variable de entorno).")
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model}:generateContent?key={config.GEMINI_API_KEY}")
        r = self._post(url, {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": temperature},
        })
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]

    # -- Utilidades --------------------------------------------------------
    @staticmethod
    def _limpiar_think(texto: str) -> str:
        return re.sub(r"<think>.*?</think>", "", texto, flags=re.DOTALL).strip()

    @staticmethod
    def _parsear_json(texto: str) -> dict:
        t = texto.strip()
        t = re.sub(r"^```(?:json)?", "", t).strip()
        t = re.sub(r"```$", "", t).strip()
        try:
            return json.loads(t)
        except json.JSONDecodeError:
            # Rescate: extrae el PRIMER objeto {...} balanceado (no greedy).
            inicio = t.find("{")
            if inicio != -1:
                nivel = 0
                for i in range(inicio, len(t)):
                    if t[i] == "{":
                        nivel += 1
                    elif t[i] == "}":
                        nivel -= 1
                        if nivel == 0:
                            return json.loads(t[inicio:i + 1])
            raise