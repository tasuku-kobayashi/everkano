"""好感度エンジンのテスト用フィクスチャ。"""

from __future__ import annotations

import pytest

from app.services.persona import Persona
from tests.engine.affinity.helpers import FakeAudit, load_test_persona


@pytest.fixture
def persona() -> Persona:
    return load_test_persona()


@pytest.fixture
def audit() -> FakeAudit:
    return FakeAudit()
