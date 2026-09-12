from typing import Annotated

from pydantic import BaseModel, Field, field_validator


class Vehicle(BaseModel):
    id: str
    brand: str
    model: str
    year: int


class Citation(BaseModel):
    manual_id: str | None = None
    version_id: str | None = None
    asset_id: str | None = None
    evidence_type: str = "pdf_text"
    manual_title: str
    chapter_title: str
    page_number: int
    excerpt: str


class ChatResponse(BaseModel):
    answer: str
    steps: list[str]
    warnings: list[str]
    citations: list[Citation]
    grounded: bool


class ChatRequest(BaseModel):
    vehicle_id: str
    question: Annotated[str, Field(min_length=1)]
    chapter_titles: list[str] | None = None

    @field_validator("chapter_titles")
    @classmethod
    def chapter_titles_must_not_be_blank(
        cls, chapter_titles: list[str] | None
    ) -> list[str] | None:
        if chapter_titles is not None and any(
            not chapter_title.strip() for chapter_title in chapter_titles
        ):
            raise ValueError("Chapter titles must not be blank.")
        return chapter_titles
