"""같은 날, 같은 설정으로 완료한 스토어만 재사용한다."""
import hashlib
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile

import config
from common.logger import get_logger
from common.models import MarketplaceProduct

logger = get_logger("naver.checkpoint")


def _checkpoint_path(site_info: dict) -> Path:
    # URL의 검색 조건까지 키에 반영하되 원문/쿼리는 메타데이터에 보관하지 않는다.
    identity = {
        "version": 1,
        "site_name": site_info["site_name"],
        "url": site_info["url"].strip(),
        "type": site_info.get("type", "smart_store"),
        "model_strategy": site_info.get("model_strategy", "auto"),
        "navigation": site_info.get("navigation", "direct"),
    }
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return Path(config.OUTPUT_DIR) / date.today().strftime("%Y%m%d") / "checkpoints" / "naver" / f"{key}.json"


def load_checkpoint(site_info: dict) -> list[MarketplaceProduct] | None:
    """완료 파일만 복원한다. 파일 없음·손상·스키마 불일치는 캐시 미스로 처리한다."""
    path = _checkpoint_path(site_info)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or data.get("status") != "complete":
            raise ValueError("incomplete checkpoint")
        records = data["products"]
        if not isinstance(records, list):
            raise ValueError("invalid products")
        products = [MarketplaceProduct(**record) for record in records]
        for product in products:
            if (product.site_name != site_info["site_name"]
                    or not isinstance(product.product_name, str)
                    or not isinstance(product.model_name, str)
                    or type(product.price) is not int
                    or product.price < 0
                    or not isinstance(product.crawled_at, str)
                    or not product.crawled_at.startswith(date.today().isoformat())
                    or (product.url is not None and not isinstance(product.url, str))
                    or product.sale_price is not None):
                raise ValueError("invalid product")
        return products
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        logger.warning("[%s] 완료 캐시를 읽을 수 없음 — 새 수집 필요", site_info["site_name"])
        return None


def save_checkpoint(site_info: dict, products: list[MarketplaceProduct]) -> Path:
    """스토어가 정상 종료한 결과를 원자적으로 저장한다. 최종 상품 파일과 분리한다."""
    path = _checkpoint_path(site_info)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"version": 1, "status": "complete", "products": [asdict(product) for product in products]}
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path
