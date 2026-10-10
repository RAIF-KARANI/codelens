"""What the LLM must return. The Pydantic model is sent to Ollama as a JSON
Schema (constrained decoding) and then used to validate the reply."""

from enum import StrEnum

from pydantic import BaseModel, Field

from core.enums import Severity


class Category(StrEnum):
    BUG = "bug"
    SECURITY = "security"
    PERFORMANCE = "performance"
    CONCURRENCY = "concurrency"
    API_CONTRACT = "api-contract"  # callers and callee disagree (signature, return, raises)
    ERROR_HANDLING = "error-handling"
    MAINTAINABILITY = "maintainability"


class FindingOut(BaseModel):
    file_path: str = Field(description="Path of the changed file, exactly as in the diff")
    line_start: int = Field(ge=1, description="First line (new version of the file)")
    line_end: int = Field(ge=1, description="Last line (new version of the file)")
    severity: Severity
    category: Category
    title: str = Field(max_length=120, description="One-line summary")
    explanation: str = Field(description="Why this is a problem, citing evidence ids")
    suggested_fix: str | None = Field(default=None, description="Concrete fix, if clear")
    evidence: list[str] = Field(
        default_factory=list,
        description='Ids of the context blocks that support this finding, e.g. ["N12"]',
    )
    confidence: float = Field(ge=0, le=1, description="How sure you are this is a real problem")


class ReviewOut(BaseModel):
    summary: str = Field(description="Two sentences on what the change does and its main risk")
    findings: list[FindingOut] = Field(
        default_factory=list, description="Only real problems; an empty list is a good answer"
    )
