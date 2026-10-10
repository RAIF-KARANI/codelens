"""Pipeline configuration: every knob of a review, in one validated object.

Configs live in configs/pipeline/*.yaml. Each review stores the exact config
it ran with (reviews.pipeline_config), so results are reproducible and an
ablation is just a different file over the same code path (planning.md §2.2).
"""

from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field


class _Section(BaseModel):
    # A typo in a YAML key must fail loudly, not silently fall back to a default.
    model_config = ConfigDict(extra="forbid")


class RetrievalConfig(_Section):
    enabled: bool = True  # False = diff-only (the A0 ablation)
    vector_k: int = Field(default=8, ge=0)  # neighbours per seed
    max_seeds: int = Field(default=24, ge=1)  # biggest changes first beyond this


class PackingConfig(_Section):
    # Token budgets per LLM call (planning.md §6.2; num_ctx 16384).
    diff_tokens: int = Field(default=4000, gt=0)
    changed_node_tokens: int = Field(default=3500, ge=0)
    context_tokens: int = Field(default=4000, ge=0)
    max_chunks: int = Field(default=4, ge=1)
    # When a context item doesn't fit whole, include just its signature line.
    degrade_to_signature: bool = True


class GenerationConfig(_Section):
    think: bool = False  # Qwen3 thinking mode (the U6 ablation)
    # Eight findings in JSON come to ~1,200 tokens; the rest of the window is prompt.
    max_output_tokens: int = Field(default=1500, gt=0)
    max_findings_per_chunk: int = Field(default=8, ge=1)


class ValidationConfig(_Section):
    min_confidence: float = Field(default=0.6, ge=0, le=1)
    max_comments: int = Field(default=5, ge=0)


class PipelineConfig(_Section):
    name: str = "b0"
    retrieval: RetrievalConfig = RetrievalConfig()
    packing: PackingConfig = PackingConfig()
    generation: GenerationConfig = GenerationConfig()
    validation: ValidationConfig = ValidationConfig()

    @classmethod
    def from_yaml(cls, path: Path) -> Self:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.model_validate(data)
