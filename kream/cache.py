"""Kream 상세 모델번호 캐시와 검색 부정 캐시.

파일 로드·조회·기록·저장만 담당한다. 검색·상세 페이지 이동은 crawler 가 한다.
"""
import json
import logging
import re
from datetime import date
from pathlib import Path

from common.atomic_io import write_json_atomic

logger = logging.getLogger(__name__)

# 새로 등록된 상품을 놓치는 최대 기간.
NEGATIVE_CACHE_TTL_DAYS = 7

KREAM_CACHE_DIR_NAME = "kream_cache"
DETAIL_CACHE_FILENAME = "detail_model_numbers.json"
NEGATIVE_CACHE_FILENAME = "search_negative.json"

REASON_NO_RESULT = "no_result"
REASON_NO_MATCH = "no_match"
NEGATIVE_REASONS = frozenset({REASON_NO_RESULT, REASON_NO_MATCH})

_CHECKED_ON_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_NEGATIVE_FIELDS = frozenset({"checked_at", "reason"})
_MISSING = object()
_BROKEN = object()


def detail_cache_key(product_url: str) -> str:
    # card.href 실측(logs/crawler_20261116.log 2195건)은 https://kream.co.kr/products/{id} 뿐이라 쿼리·끝 슬래시를 자르지 않는다.
    return product_url


def exclude_fresh_negative(
    model_names: list[str],
    cache: "KreamCache",
    today: date,
) -> list[str]:
    """유효기간 안의 부정 캐시 모델명을 빼고 남은 순서를 유지한다."""
    return [name for name in model_names if not cache.negative_is_fresh(name, today)]


def _read_json(path: Path) -> object:
    """파일이 없으면 _MISSING. 깨진 JSON 이면 경고 후 _BROKEN."""
    if not path.is_file():
        return _MISSING
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        logger.warning(
            "Kream 캐시 JSON을 읽지 못해 빈 캐시로 시작합니다: path=%s error=%s",
            path,
            type(exc).__name__,
        )
        return _BROKEN


def _valid_checked_on(value: object) -> str | None:
    if not isinstance(value, str) or _CHECKED_ON_RE.fullmatch(value) is None:
        return None
    try:
        date.fromisoformat(value)
    except ValueError:
        return None
    return value


def _valid_detail(data: object) -> dict[str, str] | None:
    if not isinstance(data, dict):
        return None
    cleaned: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not key:
            return None
        if not isinstance(value, str) or not value:
            return None
        cleaned[detail_cache_key(key)] = value
    return cleaned


def _valid_negative(data: object) -> dict[str, dict[str, str]] | None:
    if not isinstance(data, dict):
        return None
    cleaned: dict[str, dict[str, str]] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not key or not isinstance(value, dict):
            return None
        if set(value) != _NEGATIVE_FIELDS:
            return None
        checked_on = _valid_checked_on(value.get("checked_at"))
        reason = value.get("reason")
        if checked_on is None or reason not in NEGATIVE_REASONS:
            return None
        cleaned[key] = {"checked_at": checked_on, "reason": reason}
    return cleaned


def _load_mapping(path: Path, validate) -> dict:
    raw = _read_json(path)
    if raw is _MISSING or raw is _BROKEN:
        return {}
    parsed = validate(raw)
    if parsed is None:
        logger.warning("Kream 캐시 형식이 달라 빈 캐시로 시작합니다: path=%s", path)
        return {}
    return parsed


class KreamCache:
    """output/kream_cache 아래 두 JSON 파일을 메모리에 두고 원자적으로 저장한다."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.detail_models: dict[str, str] = {}
        self.negative: dict[str, dict[str, str]] = {}

    @property
    def detail_path(self) -> Path:
        return self.directory / DETAIL_CACHE_FILENAME

    @property
    def negative_path(self) -> Path:
        return self.directory / NEGATIVE_CACHE_FILENAME

    def load(self) -> None:
        """없으면 빈 캐시. JSON 이 깨졌거나 형식이 다르면 경고 후 빈 캐시로 시작한다."""
        self.detail_models = _load_mapping(self.detail_path, _valid_detail)
        self.negative = _load_mapping(self.negative_path, _valid_negative)

    def detail_model(self, product_url: str) -> str | None:
        return self.detail_models.get(detail_cache_key(product_url))

    def remember_detail_model(self, product_url: str, model_number: str) -> None:
        """상세 페이지에서 파싱한 모델번호만 넣는다. 빈 값은 파일 전체를 깨뜨리므로 거절한다."""
        key = detail_cache_key(product_url)
        if not key or not isinstance(model_number, str) or not model_number:
            return
        self.detail_models[key] = model_number

    def remember_negative(self, model_name: str, reason: str, checked_on: date) -> None:
        if reason not in NEGATIVE_REASONS:
            raise ValueError("알 수 없는 부정 캐시 사유")
        self.negative[model_name] = {
            "checked_at": checked_on.isoformat(),
            "reason": reason,
        }

    def drop_negative(self, model_name: str) -> None:
        self.negative.pop(model_name, None)

    def negative_is_fresh(self, model_name: str, today: date) -> bool:
        entry = self.negative.get(model_name)
        if entry is None:
            return False
        checked_on = date.fromisoformat(entry["checked_at"])
        age_days = (today - checked_on).days
        return 0 <= age_days < NEGATIVE_CACHE_TTL_DAYS

    def save(self) -> None:
        """두 파일을 write_json_atomic 으로 저장한다. 부모 디렉터리는 여기서 만든다."""
        self.directory.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.detail_path, self.detail_models)
        write_json_atomic(self.negative_path, self.negative)
