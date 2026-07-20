"""Model metadata types. Owner: Agent 4."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ModelInfo:
    model_id: str
    display_name: str
