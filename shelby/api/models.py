"""Pydantic request/response models."""

from typing import Literal
from pydantic import BaseModel, Field


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]
    stream: bool = False


class FileRef(BaseModel):
    name: str
    url: str


class ChatResponse(BaseModel):
    reply: str
    model: str
    files: list[FileRef] = []


class IngestRequest(BaseModel):
    text: str
    source: str = "api"


class IngestResponse(BaseModel):
    chunks_added: int
    total_docs: int


class SearchRequest(BaseModel):
    query: str
    n_results: int = Field(default=3, ge=1, le=20)


class SearchResult(BaseModel):
    text: str
    source: str
    score: float


class SearchResponse(BaseModel):
    results: list[SearchResult]
