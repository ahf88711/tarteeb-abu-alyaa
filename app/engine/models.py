"""Structured data models for the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .dates import ExtractedDate, HijriDate
from .normalize import normalize_arabic_name


class NameStatus(str, Enum):
    VERIFIED = "مؤكد"
    NEEDS_REVIEW = "يحتاج مراجعة"
    UNKNOWN = "غير معروف"
    AMBIGUOUS = "الاسم غير محسوم ويحتاج مراجعة"
    NOT_IN_MASTER = "غير موجود في الملف الرئيسي"


@dataclass
class MasterPerson:
    """A person record collected from the master PDF (all pages)."""

    original_name: str
    normalized_name: str
    rank_title: str = ""
    notes_texts: list[str] = field(default_factory=list)
    dates: list[ExtractedDate] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)
    row_indices: list[int] = field(default_factory=list)

    def add_occurrence(
        self,
        *,
        page: int,
        notes: str,
        rank_title: str = "",
        row_index: int = 0,
        date_confidence: float = 0.9,
    ) -> None:
        if page not in self.pages:
            self.pages.append(page)
        if notes:
            self.notes_texts.append(notes)
        if rank_title and not self.rank_title:
            self.rank_title = rank_title
        self.row_indices.append(row_index)
        from .dates import extract_all_dates

        for d in extract_all_dates(
            notes,
            page=page,
            confidence=date_confidence,
            person_name=self.original_name,
            source_snippet=notes[:240],
            row_index=row_index,
        ):
            self.dates.append(d)

    def to_dict(self) -> dict:
        return {
            "original_name": self.original_name,
            "normalized_name": self.normalized_name,
            "rank_title": self.rank_title,
            "pages": self.pages,
            "notes_texts": self.notes_texts,
            "dates": [d.to_dict() for d in self.dates],
        }


@dataclass
class TargetName:
    """A name extracted from the target list image/PDF."""

    id: str
    original_name: str
    normalized_name: str
    ocr_raw: str
    confidence: float
    status: NameStatus
    crop_path: Optional[str] = None
    candidates: list[dict] = field(default_factory=list)
    matched_master_name: Optional[str] = None
    user_corrected_name: Optional[str] = None
    bbox: Optional[dict] = None

    @property
    def display_name(self) -> str:
        return self.user_corrected_name or self.matched_master_name or self.original_name

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "original_name": self.original_name,
            "normalized_name": self.normalized_name,
            "ocr_raw": self.ocr_raw,
            "confidence": self.confidence,
            "status": self.status.value,
            "crop_path": self.crop_path,
            "candidates": self.candidates,
            "matched_master_name": self.matched_master_name,
            "user_corrected_name": self.user_corrected_name,
            "display_name": self.display_name,
            "bbox": self.bbox,
        }


@dataclass
class SessionState:
    """In-memory session for a ranking run."""

    session_id: str
    master_path: Optional[str] = None
    target_path: Optional[str] = None
    master_people: dict[str, MasterPerson] = field(default_factory=dict)
    target_names: list[TargetName] = field(default_factory=list)
    ranking_results: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    phase: str = "init"
    messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "phase": self.phase,
            "messages": self.messages,
            "master_loaded": bool(self.master_people),
            "master_people_count": len(self.master_people),
            "target_names": [t.to_dict() for t in self.target_names],
            "ranking_results": self.ranking_results,
            "summary": self.summary,
        }


def make_master_key(name: str) -> str:
    return normalize_arabic_name(name)
