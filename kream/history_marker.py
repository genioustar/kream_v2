"""
kream/history_marker.py
직전 날짜의 arbitrage_results.json 과 비교해 오늘 결과 항목에 저장 이력·가격 변동 표시를 붙인다.
"""
import json
import re
from pathlib import Path

from common.logger import get_logger

logger = get_logger(__name__)

DATE_FOLDER_PATTERN = re.compile(r"^\d{8}$")
RESULT_FILENAME = "arbitrage_results.json"

KEY_MODEL_NAME = "model_name"
KEY_MARKETPLACE_SITE = "marketplace_site"
KEY_MARKETPLACE_PRICE = "marketplace_price"

FIELD_PREVIOUSLY_SAVED = "previously_saved"
FIELD_PRICE_STATUS = "price_status"
FIELD_PREVIOUS_PRICE = "previous_marketplace_price"

STATUS_NEW = "new"
STATUS_SAME = "same"
STATUS_CHANGED = "changed"

PreviousPriceMap = dict[tuple[str, str], set[int]]


def find_previous_result_path(output_dir: Path) -> Path | None:
    """오늘 폴더(output_dir)를 제외하고 결과 파일이 존재하는 가장 최신 날짜 폴더의 파일 경로를 반환한다."""
    today_name = output_dir.name
    base_dir = output_dir.parent
    if not base_dir.is_dir():
        return None
    candidates = sorted(
        (
            d for d in base_dir.iterdir()
            if d.is_dir() and DATE_FOLDER_PATTERN.match(d.name) and d.name < today_name
        ),
        key=lambda d: d.name,
        reverse=True,
    )
    for folder in candidates:
        result_path = folder / RESULT_FILENAME
        if result_path.is_file():
            return result_path
    return None


def _parse_previous_item(item: object) -> tuple[tuple[str, str], int]:
    """이전 결과 레코드 한 건을 (키, 가격)으로 변환한다. 손상 레코드는 예외를 발생시킨다."""
    if not isinstance(item, dict):
        raise TypeError("레코드가 dict 가 아님")
    model_name = item[KEY_MODEL_NAME]
    marketplace_site = item[KEY_MARKETPLACE_SITE]
    if not isinstance(model_name, str) or not isinstance(marketplace_site, str):
        raise TypeError("model_name/marketplace_site 가 문자열이 아님")
    return (model_name, marketplace_site), _parse_price(item[KEY_MARKETPLACE_PRICE])


def _parse_price(raw_price: object) -> int:
    """가격 값을 int 로 변환한다. bool·정수가 아닌 float(inf/nan 포함)·변환 불가 값은 예외를 발생시킨다."""
    if isinstance(raw_price, bool):
        raise TypeError("가격이 bool 임")
    if isinstance(raw_price, float):
        if not raw_price.is_integer():
            raise ValueError("가격이 정수로 표현되지 않는 float 임")
        return int(raw_price)
    return int(raw_price)  # int, 숫자 문자열 허용. 그 외는 TypeError/ValueError


def load_previous_price_map(output_dir: Path) -> PreviousPriceMap:
    """직전 결과를 (model_name, marketplace_site) -> {marketplace_price, ...} 맵으로 로드한다.

    같은 키에 가격이 여러 개면 모두 집합에 보관한다. 이전 파일이 없으면 빈 맵(정상 분기).
    파일 파싱 실패·최상위 형식 불일치는 warning 후 빈 맵, 손상된 레코드는 해당 건만 건너뛴다.
    """
    previous_path = find_previous_result_path(output_dir)
    if previous_path is None:
        logger.info("비교할 이전 arbitrage_results.json 없음 — 모든 항목을 신규로 처리")
        return {}
    try:
        data = json.loads(previous_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("이전 결과 읽기 실패 (%s): %s — 모든 항목을 신규로 처리", previous_path, exc)
        return {}
    if not isinstance(data, list):
        logger.warning("이전 결과 형식 불일치 (%s): 최상위가 list 아님 — 모든 항목을 신규로 처리", previous_path)
        return {}
    price_map: PreviousPriceMap = {}
    skipped_count = 0
    for item in data:
        try:
            key, price = _parse_previous_item(item)
        except (ValueError, TypeError, KeyError):
            skipped_count += 1
            continue
        price_map.setdefault(key, set()).add(price)
    if skipped_count:
        logger.warning("이전 결과 손상 레코드 %d건 건너뜀 ← %s", skipped_count, previous_path)
    logger.info("이전 결과 %d건 로드 ← %s", len(data) - skipped_count, previous_path)
    return price_map


def select_closest_previous_price(previous_prices: set[int], today_price: int) -> int:
    """이전 가격 집합에서 오늘 가격과의 차이가 가장 작은 값을 고른다. 동률이면 더 작은 가격."""
    return min(previous_prices, key=lambda price: (abs(price - today_price), price))


def mark_result(result: dict, previous_prices: PreviousPriceMap) -> dict:
    """결과 dict 한 건에 previously_saved / price_status (/ previous_marketplace_price) 를 붙여 반환한다."""
    key = (result[KEY_MODEL_NAME], result[KEY_MARKETPLACE_SITE])
    if key not in previous_prices:
        result[FIELD_PREVIOUSLY_SAVED] = False
        result[FIELD_PRICE_STATUS] = STATUS_NEW
        return result
    known_prices = previous_prices[key]
    today_price = result[KEY_MARKETPLACE_PRICE]
    result[FIELD_PREVIOUSLY_SAVED] = True
    if today_price in known_prices:
        result[FIELD_PRICE_STATUS] = STATUS_SAME
    else:
        result[FIELD_PRICE_STATUS] = STATUS_CHANGED
        result[FIELD_PREVIOUS_PRICE] = select_closest_previous_price(known_prices, today_price)
    return result
