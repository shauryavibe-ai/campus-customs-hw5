"""The language model every agent uses: gpt-6-luna through Portkey (project rule).

PORTKEY_API_KEY is read from the root AI Foundations/.env and is never printed or logged.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from dotenv import dotenv_values  # noqa: E402
from openai import AsyncOpenAI  # noqa: E402
from pydantic_ai.models.openai import OpenAIResponsesModel  # noqa: E402
from pydantic_ai.providers.openai import OpenAIProvider  # noqa: E402

HW5 = Path(__file__).resolve().parent.parent
# The key is read straight from the root .env into this module only. It is NOT exported to
# os.environ, so MCP servers and other child processes never receive it.
_ENV = dotenv_values(HW5.parent / ".env")

MODEL_NAME = os.getenv("MODEL_NAME", "gpt-6-luna").strip()
PORTKEY_BASE_URL = os.getenv("PORTKEY_BASE_URL", "https://api.portkey.ai/v1").rstrip("/")


@lru_cache(maxsize=1)
def build_model() -> OpenAIResponsesModel:
    api_key = (_ENV.get("PORTKEY_API_KEY") or os.getenv("PORTKEY_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("PORTKEY_API_KEY is not set (add it to the root .env file).")
    client = AsyncOpenAI(api_key=api_key, base_url=PORTKEY_BASE_URL,
                         default_headers={"x-portkey-api-key": api_key})
    return OpenAIResponsesModel(MODEL_NAME, provider=OpenAIProvider(openai_client=client))
