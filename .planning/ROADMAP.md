# Roadmap: resell-sniper

## Milestones

- ✅ **v1.0 기초 파이프라인** - Phases 1-0 (shipped 2026-04-12)
- 🚧 **v1.1 확장 및 안정화** - Phases 1-4 (in progress)

## Phases

<details>
<summary>✅ v1.0 기초 파이프라인 - SHIPPED 2026-04-12</summary>

네이버·아디다스 크롤링 + Kream 차익 비교 전체 파이프라인 구축 완료.
모든 코드는 초기 커밋에 포함됨.

</details>

### 🚧 v1.1 확장 및 안정화 (In Progress)

**Milestone Goal:** 새 쇼핑몰 추가, 안정성 개선, 성능 최적화, 알림 기능 추가

#### Phase 1: 나이키 공홈 크롤러 추가
**Goal**: 나이키 공홈(nike.com/kr) 세일 신발 크롤러를 추가해 기존 파이프라인(diff·Kream 비교)에 자동 통합한다.
**Depends on**: 없음 (독립 실행 가능)
**Requirements**: SRC-01, SRC-02
**Success Criteria** (what must be TRUE):
  1. `make full` 실행 시 `output/YYYYMMDD/nike_products.json` 생성됨
  2. Kream 차익 비교 결과에 나이키 소스 상품이 포함됨
  3. diff 추적에 나이키 소스가 자동 포함됨
  4. Kids·비신발 카테고리 상품은 제외됨
**Plans**: TBD

Plans:
- [x] 01-01: nike/crawler.py 구현 (세일 신발 크롤링, NaverProduct 포맷)
- [ ] 01-02: main.py STEP 1 통합 및 파이프라인 연결

#### Phase 2: 안정성 및 버그 수정
**Goal**: 간헐적 셀렉터 오류·차단·부분 실패 문제를 해결해 파이프라인 완주율을 높인다.
**Depends on**: 없음 (Phase 1과 병렬 가능)
**Requirements**: STBL-01, STBL-02, STBL-03, STBL-04
**Success Criteria** (what must be TRUE):
  1. 알려진 셀렉터 오류 수정 후 재현 없음
  2. Kream 차단 시 자동 복구 후 파이프라인 완주
  3. 일부 크롤러 실패 시에도 성공한 소스 결과는 저장됨
**Plans**: TBD

Plans:
- [ ] 02-01: 셀렉터 오류 진단 및 수정 (네이버·아디다스·Kream)
- [ ] 02-02: 차단 복구 로직 개선 및 부분 결과 저장

#### Phase 3: 성능 개선
**Goal**: 전체 파이프라인 실행 시간을 단축하고 불필요한 중복 크롤링을 줄인다.
**Depends on**: Phase 1
**Requirements**: PERF-01, PERF-02, PERF-03
**Success Criteria** (what must be TRUE):
  1. 전체 파이프라인 실행 시간 단축 (기준값 대비 측정)
  2. Kream 차단 없이 동시성 2 이상 안정 동작
  3. 재실행 시 기존 파일 재사용 동작
**Plans**: TBD

Plans:
- [ ] 03-01: 네이버 무한스크롤 최적화 및 Kream 동시성 탐색
- [ ] 03-02: 부분 캐시 활용 로직 강화

#### Phase 4: 텔레그램 알림
**Goal**: 차익 결과와 오류를 텔레그램으로 자동 전송한다.
**Depends on**: Phase 2
**Requirements**: NOTF-01, NOTF-02
**Success Criteria** (what must be TRUE):
  1. `make full` 실행 후 텔레그램으로 차익 결과 요약 수신
  2. 크롤링 오류·차단 발생 시 오류 알림 수신
**Plans**: TBD

Plans:
- [ ] 04-01: common/notifier.py 구현 및 main.py 통합

## Progress

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. 나이키 공홈 크롤러 추가 | v1.1 | 1/2 | In Progress|  |
| 2. 안정성 및 버그 수정 | v1.1 | 0/2 | Not started | - |
| 3. 성능 개선 | v1.1 | 0/2 | Not started | - |
| 4. 텔레그램 알림 | v1.1 | 0/1 | Not started | - |
