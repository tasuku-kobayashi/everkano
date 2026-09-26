"""E6 の安全対応と、出力の追加検査（E2 / E3）。ENGINE_BRIEF §2.5。"""

from app.engine.safety.detector import SelfHarmDetector, normalize_for_safety
from app.engine.safety.output_guard import CATEGORY_COMMERCE_COUPLING, CATEGORY_HUMAN_CLAIM, DefaultOutputGuard
from app.engine.safety.resources import SafetyConfig, SafetyConfigError, load_safety_config
from app.engine.safety.service import DefaultSafetyService

__all__ = [
    "CATEGORY_COMMERCE_COUPLING",
    "CATEGORY_HUMAN_CLAIM",
    "DefaultOutputGuard",
    "DefaultSafetyService",
    "SafetyConfig",
    "SafetyConfigError",
    "SelfHarmDetector",
    "load_safety_config",
    "normalize_for_safety",
]
