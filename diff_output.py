"""
diff_output.py
날짜별 output/ 폴더의 같은 이름 JSON 파일을 비교하여 변경분(추가/삭제/변경)만 저장한다.

실행 방법:
  python diff_output.py                               # 가장 최근 두 날짜 자동 비교
  python diff_output.py --old 20260324 --new 20260325  # 날짜 직접 지정
  python diff_output.py --file naver_products.json     # 특정 파일만 비교
"""
import argparse
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import config
from common.logger import get_logger
from common.models import FieldChange, ItemDiff

logger = get_logger("diff_output")

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

# JSON 파일별 복합 비교 키 필드 (명시적으로 등록된 파일)
KEY_CONFIG: dict[str, tuple[str, ...]] = {
    "naver_products.json": ("model_name", "site_name"),
}

# *_products.json 중 KEY_CONFIG에 없는 파일에 적용되는 기본 키
# NaverProduct 포맷을 공유하는 모든 소스(adidas 등)에 적용 가능
DEFAULT_PRODUCTS_KEY: tuple[str, ...] = ("model_name", "site_name")

# 항상 다른 타임스탬프 필드는 비교에서 제외
IGNORE_FIELDS: set[str] = {"crawled_at", "checked_at"}

# ---------------------------------------------------------------------------
# 유틸리티
# ---------------------------------------------------------------------------

def discover_dates(output_dir: Path) -> list[str]:
    """output/ 내 YYYYMMDD 형식 디렉토리 목록을 정렬하여 반환한다."""
    dates = [
        d.name
        for d in output_dir.iterdir()
        if d.is_dir() and re.fullmatch(r"\d{8}", d.name)
    ]
    return sorted(dates)


def load_json_file(path: Path) -> list[dict]:
    """JSON 배열 파일을 읽어 dict 리스트로 반환한다."""
    return json.loads(path.read_text(encoding="utf-8"))


def build_index(items: list[dict], key_fields: tuple[str, ...]) -> dict[str, dict]:
    """복합 키로 아이템을 인덱싱한다. 키 중복 시 마지막 항목을 사용한다."""
    index: dict[str, dict] = {}
    for item in items:
        try:
            key = "|".join(str(item[k]) for k in key_fields)
        except KeyError as e:
            logger.warning("키 필드 누락: %s — 아이템 건너뜀", e)
            continue
        if key in index:
            logger.warning("중복 키 발견: %s — 마지막 항목으로 덮어씀", key)
        index[key] = item
    return index


# ---------------------------------------------------------------------------
# 핵심 diff 로직
# ---------------------------------------------------------------------------

def compute_diff(
    old_items: list[dict],
    new_items: list[dict],
    key_fields: tuple[str, ...],
) -> dict:
    """두 아이템 리스트를 비교하여 diff 결과 dict를 반환한다."""
    old_index = build_index(old_items, key_fields)
    new_index = build_index(new_items, key_fields)

    old_keys = set(old_index)
    new_keys = set(new_index)

    added_keys = new_keys - old_keys
    removed_keys = old_keys - new_keys
    common_keys = old_keys & new_keys

    changes: list[dict] = []

    for key in sorted(added_keys):
        diff = ItemDiff(key=key, change_type="added", old_item=None, new_item=new_index[key])
        changes.append(asdict(diff))

    modified_count = 0
    for key in sorted(common_keys):
        old_item = old_index[key]
        new_item = new_index[key]

        field_changes: list[FieldChange] = []
        all_fields = set(old_item) | set(new_item)
        for f in sorted(all_fields - IGNORE_FIELDS):
            old_val = old_item.get(f)
            new_val = new_item.get(f)
            if old_val != new_val:
                field_changes.append(FieldChange(field=f, old_value=old_val, new_value=new_val))

        if field_changes:
            diff = ItemDiff(
                key=key,
                change_type="modified",
                fields=field_changes,
                old_item=old_item,
                new_item=new_item,
            )
            changes.append(asdict(diff))
            modified_count += 1

    unchanged_count = len(common_keys) - modified_count
    summary = {
        "added": len(added_keys),
        "removed": len(removed_keys),
        "modified": modified_count,
        "unchanged": unchanged_count,
    }
    return {"summary": summary, "changes": changes}


# ---------------------------------------------------------------------------
# 파일/날짜 비교 오케스트레이터
# ---------------------------------------------------------------------------

def diff_date_pair(
    old_date: str,
    new_date: str,
    output_dir: Path,
    target_file: Optional[str] = None,
) -> list[Path]:
    """두 날짜 디렉토리 내의 JSON 파일들을 비교하고 diff 결과를 저장한다.

    Returns:
        저장된 diff 파일 경로 목록
    """
    old_dir = output_dir / old_date
    new_dir = output_dir / new_date

    # 두 디렉토리에 모두 존재하는 JSON 파일 목록
    old_files = {f.name for f in old_dir.glob("*.json")}
    new_files = {f.name for f in new_dir.glob("*.json")}
    common_files = old_files & new_files

    if target_file:
        if target_file not in common_files:
            logger.error("'%s' 파일이 두 날짜 모두에 존재하지 않습니다.", target_file)
            return []
        common_files = {target_file}

    only_in_old = old_files - new_files
    only_in_new = new_files - old_files
    for f in only_in_old:
        logger.info("'%s'는 %s에만 존재합니다 (비교 건너뜀)", f, old_date)
    for f in only_in_new:
        logger.info("'%s'는 %s에만 존재합니다 (비교 건너뜀)", f, new_date)

    if not common_files:
        logger.warning("두 날짜에 공통으로 존재하는 JSON 파일이 없습니다.")
        return []

    # diff 출력 디렉토리 생성
    diff_dir = output_dir / "diff" / f"{old_date}_vs_{new_date}"
    diff_dir.mkdir(parents=True, exist_ok=True)

    saved_paths: list[Path] = []

    for filename in sorted(common_files):
        key_fields = KEY_CONFIG.get(filename)
        if key_fields is None:
            if filename.endswith("_products.json"):
                key_fields = DEFAULT_PRODUCTS_KEY
                logger.info("'%s': KEY_CONFIG 미등록 — 기본 키 %s 적용", filename, DEFAULT_PRODUCTS_KEY)
            else:
                logger.warning("'%s'는 KEY_CONFIG에 없습니다. 비교 건너뜀.", filename)
                continue

        old_items = load_json_file(old_dir / filename)
        new_items = load_json_file(new_dir / filename)

        logger.info("[%s] 비교 시작: %s(%d개) → %s(%d개)",
                    filename, old_date, len(old_items), new_date, len(new_items))

        result = compute_diff(old_items, new_items, key_fields)

        output = {
            "old_date": old_date,
            "new_date": new_date,
            "source_file": filename,
            "summary": result["summary"],
            "changes": result["changes"],
        }

        stem = Path(filename).stem
        out_path = diff_dir / f"{stem}_diff.json"
        out_path.write_text(
            json.dumps(output, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        s = result["summary"]
        logger.info(
            "[%s] 완료 — 추가: %d, 삭제: %d, 변경: %d, 동일: %d → %s",
            filename, s["added"], s["removed"], s["modified"], s["unchanged"], out_path,
        )
        saved_paths.append(out_path)

    return saved_paths


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="output/ 날짜 폴더 간 JSON 변경분을 비교하여 저장합니다."
    )
    parser.add_argument(
        "--old",
        metavar="YYYYMMDD",
        help="비교 기준이 되는 과거 날짜 (기본: 두 번째로 최신 날짜)",
    )
    parser.add_argument(
        "--new",
        metavar="YYYYMMDD",
        help="비교 대상인 최신 날짜 (기본: 가장 최신 날짜)",
    )
    parser.add_argument(
        "--file",
        metavar="FILENAME",
        help="비교할 파일 이름 (기본: 공통 파일 전체)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    output_dir = Path(__file__).parent / config.OUTPUT_DIR
    dates = discover_dates(output_dir)

    if len(dates) < 2:
        logger.error("비교할 날짜 디렉토리가 2개 이상 필요합니다. 현재: %s", dates)
        return

    old_date = args.old or dates[-2]
    new_date = args.new or dates[-1]

    if old_date not in dates:
        logger.error("날짜 디렉토리를 찾을 수 없습니다: %s", old_date)
        return
    if new_date not in dates:
        logger.error("날짜 디렉토리를 찾을 수 없습니다: %s", new_date)
        return
    if old_date >= new_date:
        logger.error("--old(%s)는 --new(%s)보다 이전 날짜여야 합니다.", old_date, new_date)
        return

    logger.info("변경분 비교: %s → %s", old_date, new_date)
    saved = diff_date_pair(old_date, new_date, output_dir, target_file=args.file)

    if saved:
        logger.info("총 %d개 diff 파일 저장 완료.", len(saved))
    else:
        logger.warning("저장된 diff 파일이 없습니다.")


if __name__ == "__main__":
    main()
