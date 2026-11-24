"""Kream 상세 모델번호 캐시와 검색 부정 캐시.

파일 로드·조회·기록·저장과 부정 사유 판정을 담당한다. 검색·상세 페이지 이동은 crawler 가 한다.
"""
import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from common.atomic_io import write_json_atomic
from common.models import KreamProduct, MarketplaceProduct
from config import MIN_PRICE_DIFF, MIN_TRADE_COUNT
from kream.comparator import effective_price, is_opportunity

logger = logging.getLogger(__name__)

# 검색 결과 없음·모델번호 불일치. 새로 등록된 상품을 놓치는 최대 기간.
SEARCH_MISS_TTL_DAYS = 7
# 거래량 부족. 거래량은 며칠 사이 MIN_TRADE_COUNT 를 넘기 어렵다는 추정이라 새로 뜬 상품은 최대 이 기간만큼 늦게 발견한다.
LOW_TRADE_TTL_DAYS = 7
# 가격차 부족. Kream 가격이 거래량보다 빨리 움직인다는 추정이라 짧게 둔다.
# 판매처 최저가 하락은 이 기간과 무관하게 negative_is_fresh 가 다시 검색하게 한다.
PRICE_GAP_TTL_DAYS = 3

KREAM_CACHE_DIR_NAME = "kream_cache"
DETAIL_CACHE_FILENAME = "detail_model_numbers.json"
NEGATIVE_CACHE_FILENAME = "search_negative.json"

REASON_NO_RESULT = "no_result"
REASON_NO_MATCH = "no_match"
REASON_LOW_TRADE = "low_trade"
REASON_PRICE_GAP = "price_gap"

# 사유 목록과 유효기간의 단일 출처. 로드 검증·제외·로그가 이 표를 따른다.
NEGATIVE_TTL_DAYS: dict[str, int] = {
    REASON_NO_RESULT: SEARCH_MISS_TTL_DAYS,
    REASON_NO_MATCH: SEARCH_MISS_TTL_DAYS,
    REASON_LOW_TRADE: LOW_TRADE_TTL_DAYS,
    REASON_PRICE_GAP: PRICE_GAP_TTL_DAYS,
}

NegativeEntry = dict[str, str | int]

# price_gap 만 기록 당시 가격 둘을 함께 저장한다. 판매처 최저가가 그보다 내려가면 유효기간 안이어도 다시 검색한다.
_PRICE_REASONS = frozenset({REASON_PRICE_GAP})
_PRICE_FIELDS = frozenset({"kream_price", "marketplace_price"})

# low_trade·price_gap 판정은 config 기준(MIN_TRADE_COUNT·MIN_PRICE_DIFF)에 의존하므로 기록 시점 기준값을 항목에 함께 저장한다.
# 기준이 바뀌면 옛 판정이 차익 모델을 가릴 수 있어 negative_is_fresh 가 다시 검색한다.
LOW_TRADE_CRITERION_FIELD = "min_trade_count"
PRICE_GAP_CRITERION_FIELD = "min_price_diff"

# 한 청크에서 거래량 0 인 검증 결과가 이 비율을 넘으면 거래량 문구 형식이 바뀌어 0 으로 잘못 읽은 것으로 보고 low_trade 를 기록하지 않는다.
# 근거: logs 실측 검증 결과의 거래량 0 비율은 날짜별 최대 48%, 청크별 최대 76%(86개 청크, 20261116 청크 12). 그 위로 둔다.
# 넘겨서 오탐해도 low_trade 기록만 건너뛰어 검색이 늘 뿐 모델을 가리지 않고, 오염을 놓치면 7일간 정상 모델을 가린다.
LOW_TRADE_ZERO_RATIO_LIMIT = 0.8
# 표본이 적은 청크(마지막 청크 등)는 우연히 0 이 몰릴 수 있어 비율을 판단하지 않는다.
LOW_TRADE_ZERO_MIN_SAMPLE = 20

_CHECKED_ON_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_NEGATIVE_FIELDS = frozenset({"checked_at", "reason"})
_MISSING = object()
_BROKEN = object()


def detail_cache_key(product_url: str) -> str:
    # card.href 실측(logs/crawler_20261116.log 2195건)은 https://kream.co.kr/products/{id} 뿐이라 쿼리·끝 슬래시를 자르지 않는다.
    return product_url


def lowest_prices_by_model(products: list[MarketplaceProduct]) -> dict[str, int]:
    """모델명(앞뒤 공백 제거)별 판매처 최저 실구매가. 검색 대상 모델명과 같은 키를 쓴다."""
    lowest: dict[str, int] = {}
    for product in products:
        model_name = product.model_name.strip()
        if not model_name:
            continue
        price = effective_price(product)
        lowest[model_name] = min(price, lowest.get(model_name, price))
    return lowest


def exclude_fresh_negative(
    model_names: list[str],
    cache: "KreamCache",
    today: date,
    lowest_prices: Mapping[str, int],
) -> tuple[list[str], dict[str, int]]:
    """유효기간 안의 부정 캐시 모델명을 빼고 (남은 목록, 사유별 제외 개수)를 반환한다.

    남은 목록은 순서를 유지한다. 제외 개수는 NEGATIVE_TTL_DAYS 의 모든 사유를 키로 가진다.
    lowest_prices 에 없는 모델은 현재 판매처 가격을 모르므로 price_gap 을 믿지 않고 다시 검색한다.
    """
    kept: list[str] = []
    excluded_by_reason = dict.fromkeys(NEGATIVE_TTL_DAYS, 0)
    for name in model_names:
        if cache.negative_is_fresh(name, today, lowest_prices.get(name)):
            excluded_by_reason[str(cache.negative[name]["reason"])] += 1
        else:
            kept.append(name)
    return kept, excluded_by_reason


@dataclass(frozen=True)
class NegativeVerdict:
    """차익 조건에서 탈락한 사유. 가격 필드는 price_gap 에만 있다."""

    reason: str
    kream_price: int | None = None
    marketplace_price: int | None = None


def judge_unprofitable(
    kream_products: list[KreamProduct],
    lowest_price: int,
) -> NegativeVerdict | None:
    """모델번호 검증을 통과한 Kream 상품(1개 이상)을 판매처 최저가와 비교한다.

    차익 조건(comparator.is_opportunity)을 통과한 상품이 하나라도 있으면 None 이다.
    통과 상품이 없고 거래량이 충분한 상품도 없으면 low_trade, 있으면 price_gap 이다.
    price_gap 은 거래량이 충분한 상품 중 Kream 가격이 가장 높은 쪽을 기록한다.
    """
    enough_trade: list[KreamProduct] = []
    for kream in kream_products:
        if is_opportunity(lowest_price, kream.kream_price, kream.trade_count):
            return None
        if kream.trade_count >= MIN_TRADE_COUNT:
            enough_trade.append(kream)
    if not enough_trade:
        return NegativeVerdict(REASON_LOW_TRADE)
    closest = max(enough_trade, key=lambda kream: kream.kream_price)
    return NegativeVerdict(
        REASON_PRICE_GAP,
        kream_price=closest.kream_price,
        marketplace_price=lowest_price,
    )


def _is_zero_trade_dominant(kream_products_map: Mapping[str, list[KreamProduct]]) -> bool:
    """검증 통과 결과 중 거래량 0 인 비율이 한도를 넘으면 True. 표본이 적으면 판단하지 않고 False.

    넘으면 거래량 문구 형식이 바뀌어 0 으로 잘못 읽은 것으로 보고 WARNING 한 줄을 남긴다.
    """
    verified = [kream for products in kream_products_map.values() for kream in products]
    if len(verified) < LOW_TRADE_ZERO_MIN_SAMPLE:
        return False
    zero_count = sum(1 for kream in verified if kream.trade_count == 0)
    zero_ratio = zero_count / len(verified)
    if zero_ratio <= LOW_TRADE_ZERO_RATIO_LIMIT:
        return False
    logger.warning(
        "거래량 0 비율 %.0f%%(%s/%s개)가 한도 %.0f%% 를 넘어 거래량 문구 형식 변경이 의심됩니다 — 이 청크의 low_trade 기록을 건너뜁니다",
        zero_ratio * 100,
        zero_count,
        len(verified),
        LOW_TRADE_ZERO_RATIO_LIMIT * 100,
    )
    return True


def record_unprofitable_models(
    cache: "KreamCache",
    kream_products_map: Mapping[str, list[KreamProduct]],
    lowest_prices: Mapping[str, int],
    today: date,
) -> None:
    """검증 통과 Kream 결과가 있는 모델을 판정해 부정 캐시에 기록한다.

    빈 결과(결과 없음·불일치)는 crawler 가 이미 기록했으니 건드리지 않는다.
    차익 조건을 통과한 모델은 기록하지 않고 기존 부정 항목도 지운다.
    거래량 0 이 청크를 지배하면(_is_zero_trade_dominant) low_trade 는 기록하지 않는다. price_gap·통과 모델 삭제는 그대로다.
    crawler 의 일치 시 삭제는 검색 중에 일어나므로, 이 함수는 그 뒤에 호출해야 새 기록이 남는다.
    """
    recorded = dict.fromkeys((REASON_LOW_TRADE, REASON_PRICE_GAP), 0)
    passed = 0
    skip_low_trade = _is_zero_trade_dominant(kream_products_map)
    for model_name, kream_products in kream_products_map.items():
        if not kream_products:
            continue
        lowest_price = lowest_prices.get(model_name)
        if lowest_price is None:
            logger.warning("[%s] 판매처 가격을 찾지 못해 부정 캐시 판정을 건너뜁니다", model_name)
            continue
        verdict = judge_unprofitable(kream_products, lowest_price)
        if verdict is None:
            cache.drop_negative(model_name)
            passed += 1
            continue
        if skip_low_trade and verdict.reason == REASON_LOW_TRADE:
            continue
        cache.remember_negative(
            model_name,
            verdict.reason,
            today,
            kream_price=verdict.kream_price,
            marketplace_price=verdict.marketplace_price,
        )
        recorded[verdict.reason] += 1
        logger.debug("[%s] 부정 캐시 기록: %s", model_name, verdict.reason)
    logger.info(
        "부정 캐시 기록 — low_trade %s개, price_gap %s개, 차익 조건 통과 %s개",
        recorded[REASON_LOW_TRADE],
        recorded[REASON_PRICE_GAP],
        passed,
    )


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


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _criterion(reason: str) -> tuple[str, int] | None:
    """기준에 의존하는 사유의 (항목 필드명, 현재 config 기준값). 그 밖의 사유는 None.

    모듈 상수를 호출할 때마다 읽는다.
    """
    if reason == REASON_LOW_TRADE:
        return LOW_TRADE_CRITERION_FIELD, MIN_TRADE_COUNT
    if reason == REASON_PRICE_GAP:
        return PRICE_GAP_CRITERION_FIELD, MIN_PRICE_DIFF
    return None


def _valid_negative_entry(value: dict, reason: str) -> NegativeEntry | None:
    """알려진 사유 항목의 필드 집합·날짜·정수 형식을 검사한다. 어긋나면 None.

    기준값 필드는 없어도 받는다. 없는 항목은 negative_is_fresh 가 유효하지 않게 보고 다시 검색한다.
    """
    price_fields = _PRICE_FIELDS if reason in _PRICE_REASONS else frozenset()
    criterion = _criterion(reason)
    criterion_fields = frozenset() if criterion is None else frozenset({criterion[0]})
    required = _NEGATIVE_FIELDS | price_fields
    if not required <= set(value) <= required | criterion_fields:
        return None
    checked_on = _valid_checked_on(value["checked_at"])
    if checked_on is None:
        return None
    entry: NegativeEntry = {"checked_at": checked_on, "reason": reason}
    for field in sorted(price_fields | (criterion_fields & set(value))):
        if not _is_int(value[field]):
            return None
        entry[field] = value[field]
    return entry


def _valid_negative(data: object) -> dict[str, NegativeEntry] | None:
    """형식이 어긋나면 None(파일 전체를 빈 캐시로). 알 수 없는 사유 항목만 경고 후 건너뛴다."""
    if not isinstance(data, dict):
        return None
    cleaned: dict[str, NegativeEntry] = {}
    unknown_reasons: list[str] = []
    for key, value in data.items():
        if not isinstance(key, str) or not key or not isinstance(value, dict):
            return None
        if "reason" not in value:
            return None
        reason = value["reason"]
        if not isinstance(reason, str) or reason not in NEGATIVE_TTL_DAYS:
            unknown_reasons.append(repr(reason))
            continue
        entry = _valid_negative_entry(value, reason)
        if entry is None:
            return None
        cleaned[key] = entry
    if unknown_reasons:
        logger.warning(
            "알 수 없는 부정 캐시 사유 항목 %s개를 무시합니다(다시 검색): reasons=%s",
            len(unknown_reasons),
            sorted(set(unknown_reasons)),
        )
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
        self.negative: dict[str, NegativeEntry] = {}

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

    def remember_negative(
        self,
        model_name: str,
        reason: str,
        checked_on: date,
        *,
        kream_price: int | None = None,
        marketplace_price: int | None = None,
    ) -> None:
        """price_gap 은 두 가격이 필수이고 다른 사유는 가격을 받지 않는다.

        low_trade·price_gap 은 기록 시점의 config 기준값을 항목에 함께 저장한다.
        """
        if reason not in NEGATIVE_TTL_DAYS:
            raise ValueError("알 수 없는 부정 캐시 사유")
        entry: NegativeEntry = {"checked_at": checked_on.isoformat(), "reason": reason}
        prices = {"kream_price": kream_price, "marketplace_price": marketplace_price}
        if reason in _PRICE_REASONS:
            if not all(_is_int(price) for price in prices.values()):
                raise ValueError("price_gap 은 kream_price 와 marketplace_price 정수가 필요하다")
            entry.update(prices)
        elif any(price is not None for price in prices.values()):
            raise ValueError("가격은 price_gap 에만 기록한다")
        criterion = _criterion(reason)
        if criterion is not None:
            field, current = criterion
            entry[field] = current
        self.negative[model_name] = entry

    def drop_negative(self, model_name: str) -> None:
        self.negative.pop(model_name, None)

    def negative_is_fresh(
        self,
        model_name: str,
        today: date,
        lowest_price: int | None = None,
    ) -> bool:
        """사유별 유효기간 안(0 이상 TTL 미만)이면 True. 미래 날짜는 만료다.

        low_trade·price_gap 은 기록 시점 기준값이 현재 config 와 다르거나 항목에 없으면 유효기간 안이어도 False 다.
        price_gap 은 현재 판매처 최저가가 기록 당시보다 낮거나 모르면(None) 유효기간 안이어도 False 다.
        """
        entry = self.negative.get(model_name)
        if entry is None:
            return False
        checked_on = date.fromisoformat(str(entry["checked_at"]))
        age_days = (today - checked_on).days
        reason = str(entry["reason"])
        if not 0 <= age_days < NEGATIVE_TTL_DAYS[reason]:
            return False
        criterion = _criterion(reason)
        if criterion is not None:
            field, current = criterion
            if entry.get(field) != current:
                return False
        if reason in _PRICE_REASONS:
            return lowest_price is not None and lowest_price >= entry["marketplace_price"]
        return True

    def save(self) -> None:
        """두 파일을 write_json_atomic 으로 저장한다. 부모 디렉터리는 여기서 만든다."""
        self.directory.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.detail_path, self.detail_models)
        write_json_atomic(self.negative_path, self.negative)
