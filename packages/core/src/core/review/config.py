"""Pipeline configuration: every knob of a review, in one validated object.

Configs live in configs/pipeline/*.yaml. Each review stores the exact config
it ran with (reviews.pipeline_config), so results are reproducible and an
ablation is just a different file over the same code path (planning.md §2.2).
"""

from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Section(BaseModel):
    # A typo in a YAML key must fail loudly, not silently fall back to a default.
    model_config = ConfigDict(extra="forbid")


class RetrievalConfig(_Section):
    enabled: bool = True  # False = diff-only (the A0 ablation)
    vector_k: int = Field(default=8, ge=0)  # neighbours per seed
    max_seeds: int = Field(default=24, ge=1)  # biggest changes first beyond this


class PackingConfig(_Section):
    # How the prompt space left after the fixed parts (system prompt, PR header,
    # output reserve) is split. Measured (planning.md D64): with fixed budgets
    # sized for 16k, an 8k window was filled by diff + changed code, and
    # context, the point of retrieval, got 0 blocks in 3 of 4 chunks. Shares
    # guarantee context a real slice of any window.
    diff_share: float = Field(default=0.40, gt=0, le=1)
    changed_share: float = Field(default=0.25, ge=0, le=1)
    context_share: float = Field(default=0.35, ge=0, le=1)
    # Caps per LLM call: never more than this, however large the window.
    diff_tokens: int = Field(default=4000, gt=0)
    changed_node_tokens: int = Field(default=3500, ge=0)
    context_tokens: int = Field(default=4000, ge=0)
    max_chunks: int = Field(default=4, ge=1)
    # When a context item doesn't fit whole, include just its signature line.
    degrade_to_signature: bool = True
    # Test files' changes count this much when choosing which chunks to review
    # under max_chunks, so large test diffs don't crowd out the code they test.
    test_file_weight: float = Field(default=0.25, ge=0, le=1)

    @model_validator(mode="after")
    def _shares_fit(self) -> Self:
        if self.diff_share + self.changed_share + self.context_share > 1.0 + 1e-9:
            raise ValueError("diff_share + changed_share + context_share must be <= 1")
        return self

    def scaled_to(self, available_tokens: int) -> Self:
        """Budgets for a window with `available_tokens` of prompt space."""
        return self.model_copy(
            update={
                "diff_tokens": max(
                    1, min(self.diff_tokens, int(available_tokens * self.diff_share))
                ),
                "changed_node_tokens": min(
                    self.changed_node_tokens, int(available_tokens * self.changed_share)
                ),
                "context_tokens": min(
                    self.context_tokens, int(available_tokens * self.context_share)
                ),
            }
        )


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
