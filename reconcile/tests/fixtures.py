import json
from pathlib import Path
from typing import Any

import anymarkup

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class Fixtures:
    def __init__(self, base_path: str) -> None:
        self.base = FIXTURES_DIR / base_path

    def path(self, fixture: str) -> Path:
        return self.base / fixture

    def get(self, fixture: str) -> str:
        return self.path(fixture).read_text(encoding="locale").strip()

    def get_anymarkup(self, fixture: str) -> Any:
        return anymarkup.parse(self.get(fixture), force_types=None)

    def get_json(self, fixture: str) -> str:
        return json.dumps(self.get_anymarkup(fixture))
