"""Swappable LLM provider interface for structured extraction and agent reasoning."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel

if TYPE_CHECKING:
    from app.schemas.agent import AgentTurnDecision

TModel = TypeVar("TModel", bound=BaseModel)


class LLMProviderError(RuntimeError):
    """Raised when an LLM provider call or structured parse fails."""


class LLMProvider(ABC):
    """Single provider-agnostic interface so Gemini Flash can be swapped cleanly."""

    model_name: str
    is_offline_fallback: bool = False

    @abstractmethod
    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_cls: type[TModel],
    ) -> TModel:
        """Return a Pydantic-validated instance produced from JSON output."""

    def plan_agent_step(
        self,
        *,
        system_prompt: str,
        transcript: str,
    ) -> "AgentTurnDecision":
        """Choose the next tool call or grounded final answer for the KAP Agent loop."""
        raise NotImplementedError(f"{type(self).__name__} does not implement plan_agent_step.")
