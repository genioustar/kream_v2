---
status: testing
phase: 01-nike-crawler
source: [01-01-SUMMARY.md, 01-02-SUMMARY.md]
started: "2026-04-15T05:00:00.000Z"
updated: "2026-04-15T05:05:00.000Z"
---

## Current Test

number: 2
name: nike_products.json 내용 확인
expected: |
  생성된 nike_products.json을 열면 상품 목록이 JSON 배열로 들어있고,
  각 항목에 model_name, price, url, site_name: "nike" 필드가 존재한다.
awaiting: user response

## Tests

### 1. Cold Start Smoke Test
expected: 기존에 실행 중인 프로세스 없이 `make nike` 를 처음 실행했을 때 에러 없이 크롤러가 실행되고 `output/YYYYMMDD/nike_products.json` 파일이 생성된다.
result: issue
reported: "더 많은 데이터가 있는데 스크롤을 해도 21개만 가져오는 현상 발생"
severity: major

### 2. nike_products.json 내용 확인
expected: 생성된 `nike_products.json`을 열면 상품 목록이 JSON 배열로 들어있고, 각 항목에 `model_name`, `price`, `url`, `site_name: "nike"` 필드가 존재한다.
result: [pending]

### 3. Kids 상품 제외 확인
expected: `nike_products.json` 내용을 보면 "kids", "어린이", "유아", "주니어" 키워드가 포함된 상품명이 없다.
result: [pending]

### 4. make full — 나이키 포함 파이프라인 완주
expected: `make full` 실행 시 STEP 1에서 나이키 크롤링이 수행되고(또는 이미 파일 존재 시 스킵 메시지 출력), 파이프라인이 중단 없이 끝까지 완료된다.
result: [pending]

### 5. Kream 차익 비교에 나이키 포함
expected: `make full` 완료 후 Kream 차익 비교 결과(output 디렉토리의 diff 또는 result 파일)에 `site_name: nike` 상품이 포함되어 있다.
result: [pending]

### 6. 기존 파일 재사용(스킵) 동작
expected: 이미 오늘자 `nike_products.json`이 존재할 때 `make full`을 재실행하면 나이키 크롤링을 새로 실행하지 않고 "스킵" 또는 기존 파일 재사용 로그가 출력된다.
result: [pending]

## Summary

total: 6
passed: 0
issues: 1
pending: 5
skipped: 0

## Gaps

- truth: "무한스크롤로 페이지 전체 상품을 수집해야 한다 (21개 이상)"
  status: failed
  reason: "User reported: 더 많은 데이터가 있는데 스크롤을 해도 21개만 가져오는 현상 발생"
  severity: major
  test: 1
  artifacts: []
  missing: []
