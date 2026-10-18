"""Validate board configuration without network access or database dependencies."""
import re
from pathlib import Path
from typing import Literal
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from agent.settings import ROOT


class Board(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company: str = Field(min_length=1)
    platform: Literal['greenhouse', 'lever', 'ashby']
    board: str = Field(min_length=1)
    enabled: bool = True
    verification_status: Literal['pending', 'verified'] = 'pending'
    verified_at: str | None = None
    evidence_url: str | None = None
    notes: str = ''

    @field_validator('board')
    @classmethod
    def safe_identifier(cls, value):
        if not re.fullmatch(r'[A-Za-z0-9_-]+', value):
            raise ValueError('board must be an identifier, not a URL or path')
        return value


    @model_validator(mode='after')
    def verified_evidence(self):
        if self.verification_status == 'verified':
            from datetime import datetime
            from urllib.parse import urlsplit
            if not self.verified_at or not self.evidence_url:
                raise ValueError('Verified boards require verified_at and evidence_url')
            timestamp = datetime.fromisoformat(self.verified_at.replace('Z','+00:00'))
            if timestamp.tzinfo is None:
                raise ValueError('verified_at must include its timezone')
            parsed = urlsplit(self.evidence_url)
            if parsed.scheme != 'https' or not parsed.hostname:
                raise ValueError('evidence_url must be an HTTPS employer careers link')
        return self


def load_boards(path=None):
    """Invalid entries are isolated; whole-file YAML errors are actionable failures."""
    path = Path(path or ROOT / 'config/company_boards.yaml')
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or not isinstance(data.get('boards'), list):
        raise ValueError('company_boards.yaml must contain a boards list')
    valid, errors, seen = [], [], set()
    for index, entry in enumerate(data['boards'], 1):
        try:
            board = Board.model_validate(entry)
            key = (board.platform, board.board.lower())
            if key in seen:
                raise ValueError('duplicate platform/board entry')
            seen.add(key)
            valid.append(board.model_dump())
        except (ValidationError, ValueError):
            errors.append(f'Registry entry {index} is invalid or duplicated; check company, platform and board identifier')
    return valid, errors
