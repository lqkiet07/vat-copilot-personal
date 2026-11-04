import os
from datetime import datetime

DATA_FREEZE_DATE: str = "2026-09-30"


def today_iso() -> str:
    return datetime.now().date().isoformat()


def default_as_of_date() -> str:
    return min(today_iso(), DATA_FREEZE_DATE)


def is_vector_enabled() -> bool:
    return os.getenv("VECTOR_ENABLED", "1").lower() not in ("0", "false", "no")


def is_strict_grounding() -> bool:
    return os.getenv("STRICT_GROUNDING", "0").lower() in ("1", "true", "yes")
