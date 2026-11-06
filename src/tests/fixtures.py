"""Helpers for loading test fixture files."""

import json
from collections.abc import Callable
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

import anymarkup

OcResultFactory = Callable[..., CompletedProcess[str]]

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class Fixtures:
    """Loader for fixture files under a subdirectory of the fixtures tree."""

    def __init__(self, base_path: str) -> None:
        """Root lookups at ``base_path`` inside the fixtures directory."""
        self.base = FIXTURES_DIR / base_path

    def path(self, fixture: str) -> Path:
        """Return the path of a fixture file."""
        return self.base / fixture

    def get(self, fixture: str) -> str:
        """Return a fixture file's contents, stripped."""
        return self.path(fixture).read_text(encoding="locale").strip()

    def get_anymarkup(self, fixture: str) -> Any:
        """Return a fixture file parsed with anymarkup."""
        return anymarkup.parse(self.get(fixture), force_types=None)

    def get_json(self, fixture: str) -> str:
        """Return a fixture file re-serialized as JSON."""
        return json.dumps(self.get_anymarkup(fixture))
