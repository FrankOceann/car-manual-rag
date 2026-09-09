from pydantic import BaseModel


class Vehicle(BaseModel):
    id: str
    brand: str
    model: str
    year: int


class Citation(BaseModel):
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
