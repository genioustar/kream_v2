from dataclasses import dataclass, field
from typing import Optional


@dataclass
class NaverProduct:
    site_name: str
    product_name: str
    model_name: str
    price: int
    url: Optional[str]
    crawled_at: str


@dataclass
class KreamProduct:
    model_name: str
    kream_name: str
    kream_price: int
    trade_count: int
    kream_url: str


@dataclass
class ArbitrageResult:
    model_name: str
    naver_site: str
    naver_price: int
    kream_price: int
    price_diff: int
    trade_count: int
    naver_url: Optional[str]
    kream_url: str
    checked_at: str


@dataclass
class FieldChange:
    field: str
    old_value: Optional[object]
    new_value: Optional[object]


@dataclass
class ItemDiff:
    key: str
    change_type: str  # "added" | "removed" | "modified"
    fields: list = field(default_factory=list)  # list[FieldChange], empty for added/removed
    old_item: Optional[dict] = None
    new_item: Optional[dict] = None
