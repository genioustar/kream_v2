---
phase: 01-nike-crawler
plan: "01"
subsystem: nike-crawler
tags: [crawler, nike, playwright, stealth, infinite-scroll]
dependency_graph:
  requires: [common/browser.py, common/models.py, common/logger.py, config.py]
  provides: [nike/crawler.py, nike/__init__.py, config.NIKE_SALE_URL]
  affects: [main.py (01-02 플랜에서 통합 예정)]
tech_stack:
  added: []
  patterns: [create_browser+new_stealth_page, JS evaluate 일괄 파싱, 무한스크롤, 스타일코드 추출]
key_files:
  created:
    - nike/__init__.py (2줄)
    - nike/crawler.py (373줄)
  modified:
    - config.py (NIKE_SALE_URL 상수 1줄 추가)
    - ARCHITECTURE.md (nike 모듈 설명 추가)
decisions:
  - "CDP 대신 create_browser+new_stealth_page 사용 — 나이키는 Akamai 없음, 일반 stealth로 충분"
  - "스타일코드(CN8490-002) 우선 모델명 추출 — Kream 검색 정확도 최대화"
  - "성인 전용 URL + 키워드 2차 필터 — Kids 완전 제외"
metrics:
  duration_minutes: 2
  completed_date: "2026-04-15"
  tasks_completed: 2
  files_changed: 4
---

# Phase 1 Plan 01: nike/crawler.py 구현 Summary

나이키 코리아 세일 신발 페이지 크롤러를 create_browser+new_stealth_page 조합으로 구현 — 무한스크롤 전체 로드 후 JS 일괄 파싱, 스타일코드 우선 모델명 추출, Kids 2차 키워드 필터링 포함.

## Completed Tasks

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | nike 패키지 생성 + config.NIKE_SALE_URL 추가 | 50e3ece | nike/__init__.py, config.py |
| 2 | nike/crawler.py 본체 작성 | 9145766 | nike/crawler.py |

## Artifacts

### 생성된 파일

| 파일 | 라인 수 | 내용 |
|------|---------|------|
| `nike/__init__.py` | 2 | 패키지 마커 (docstring만) |
| `nike/crawler.py` | 373 | crawl_nike() 코루틴 + 헬퍼 5개 + 단독 실행 진입점 |

### 수정된 파일

| 파일 | 변경 내용 |
|------|----------|
| `config.py` | NIKE_SALE_URL 상수 1줄 추가 (OUTPUT_DIR 다음 줄) |
| `ARCHITECTURE.md` | nike/ 모듈 디렉토리 구조 및 설명 추가 |

## 상품 카드 셀렉터 후보 목록

| 순서 | 셀렉터 | 선택 이유 |
|------|--------|----------|
| 1 | `.product-card` | 나이키 공식 클래스명, 가장 안정적 |
| 2 | `div[class*="product-card"]` | 클래스명 변형 대응 |
| 3 | `[data-testid*="product-card"]` | data-testid 기반 폴백 |

## Kids 필터 키워드 목록

`EXCLUDE_KEYWORDS = ("kids", "어린이", "유아", "주니어")`

- 1차 제외: 성인 전용 URL(`config.NIKE_SALE_URL`) — Kids 카테고리 자체를 URL 파라미터로 배제
- 2차 제외: 카드 innerText + subtitle 합산 후 lowercase 키워드 매칭

## 모델명 추출 3단계 우선순위

1. **스타일코드 패턴 우선** (`STYLE_CODE_RE = r'\b[A-Z]{2}\d{4}-\d{3}\b'`) — card_text에서 매칭 시 반환 (예: CN8490-002)
2. **href 마지막 path 세그먼트** — URL 끝 세그먼트에 스타일코드 패턴 매칭 시 반환
3. **card_text 첫 줄 fallback** — 스타일코드 없을 때 상품명 첫 줄 반환. 빈 문자열이면 None

## 단위 검증 결과

```
OK - 모든 단위 검증 통과
```

| 검증 항목 | 결과 |
|----------|------|
| crawl_nike 코루틴 함수 여부 | PASS |
| _parse_price('159,000원') == 159000 | PASS |
| _parse_price('169,000') == 169000 | PASS |
| _parse_price(None) is None | PASS |
| _parse_price('가격문의') is None | PASS |
| _extract_model_name — 스타일코드 우선 | PASS |
| _extract_model_name — href에서 스타일코드 | PASS |
| _extract_model_name — 첫 줄 fallback | PASS |
| _extract_model_name — 빈 문자열 → None | PASS |
| _should_exclude — Kids 영문 | PASS |
| _should_exclude — 어린이 한글 | PASS |
| _should_exclude — 성인 상품 False | PASS |
| _should_exclude — subtitle 주니어 | PASS |
| SITE_NAME == 'nike' | PASS |
| async_playwright() 직접 사용 없음 | PASS |
| from playwright_stealth 직접 import 없음 | PASS |
| TODO 주석 없음 | PASS |
| module load 오류 없음 | PASS |

## Deviations from Plan

None — 플랜이 정확히 대로 실행됨.

CLAUDE.md 규칙에 따라 ARCHITECTURE.md 업데이트 수행 (플랜 명시 없었으나 소스 추가 시 필수).

## 주의사항

**실제 nike.com DOM 검증은 이 플랜에서 수행되지 않았음.**

01-02 플랜의 단독 실행 단계(`python nike/crawler.py` 또는 `make nike`)에서 실제 네트워크 요청을 통해 다음 항목을 확인해야 한다:

- `.product-card` 셀렉터가 실제 DOM에 존재하는지
- 무한스크롤이 정상 동작하는지 (카드 수 증가 확인)
- 가격·상품명·URL 파싱 결과가 실제와 일치하는지
- 봇 탐지/리다이렉트 없이 세일 페이지에 정상 도달하는지
- 스타일코드가 card_text에 노출되는지 (한국어 사이트 특성상 미노출 가능)

## Known Stubs

없음 — 모든 기능이 실제 구현됨. 단, 실제 DOM 셀렉터 유효성은 네트워크 테스트 필요.

## Self-Check: PASSED

| 항목 | 결과 |
|------|------|
| nike/__init__.py 존재 | FOUND |
| nike/crawler.py 존재 | FOUND |
| 01-01-SUMMARY.md 존재 | FOUND |
| 커밋 50e3ece 존재 | FOUND |
| 커밋 9145766 존재 | FOUND |
