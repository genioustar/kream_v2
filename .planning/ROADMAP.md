# Roadmap: resell-sniper

**Milestone 2:** 새 쇼핑몰 추가 · 버그 수정 · 성능 개선
**Status:** Planning

---

## Milestone 1 — 기초 파이프라인 (완료)

네이버·아디다스 크롤링 + Kream 차익 비교 전체 파이프라인 구축.

**완료일:** 2026-04-12

---

## Milestone 2 — 확장 및 안정화

### Phase 1 — 새 쇼핑몰 크롤러 추가

**Goal:** 기존 파이프라인과 자동 통합되는 새 쇼핑몰 크롤러를 추가한다.

**Requirements:** SRC-01, SRC-02

**Deliverables:**
- `{쇼핑몰}/crawler.py` — `crawl_{쇼핑몰}()` 구현, NaverProduct 포맷 반환
- `main.py` STEP 1 통합
- 크롤링 결과 `output/YYYYMMDD/{쇼핑몰}_products.json` 저장 확인

**Success Criteria:**
- [ ] 새 소스 크롤링 후 결과 JSON 생성
- [ ] `make full` 실행 시 새 소스 포함하여 전체 파이프라인 완주
- [ ] diff·차익 비교에 새 소스 자동 포함

---

### Phase 2 — 안정성 및 버그 수정

**Goal:** 간헐적 셀렉터 오류·차단·부분 실패 문제를 해결해 파이프라인 완주율을 높인다.

**Requirements:** STBL-01, STBL-02, STBL-03, STBL-04

**Deliverables:**
- 셀렉터 오류 수정 (네이버·아디다스·Kream)
- Kream 차단 복구 로직 개선
- 부분 결과 저장 (크롤러별 독립 실패 허용)

**Success Criteria:**
- [ ] 알려진 셀렉터 오류 재현 후 수정 확인
- [ ] Kream 차단 시나리오 시뮬레이션 후 자동 복구 확인
- [ ] 일부 크롤러 실패 시에도 성공한 소스 결과는 저장됨

---

### Phase 3 — 성능 개선

**Goal:** 전체 파이프라인 실행 시간을 단축하고, 불필요한 중복 크롤링을 줄인다.

**Requirements:** PERF-01, PERF-02, PERF-03

**Deliverables:**
- 네이버 무한스크롤 최적화
- Kream 동시성 안전 최대값 탐색 및 적용
- 부분 캐시 활용 (오늘자 파일 있으면 재사용 — 기존 로직 강화)

**Success Criteria:**
- [ ] 전체 파이프라인 실행 시간 측정 후 개선 비교
- [ ] Kream 차단 없이 동시성 2 이상 안정 동작 확인
- [ ] 재실행 시 기존 파일 재사용 동작 확인

---

### Phase 4 — 알림 기능

**Goal:** 차익 결과와 오류를 텔레그램으로 자동 전송한다.

**Requirements:** NOTF-01, NOTF-02

**Deliverables:**
- `common/notifier.py` — 텔레그램 메시지 전송
- `main.py` 통합 — 차익 결과 요약 전송 (STEP 3 완료 후)
- 크롤링 오류·차단 발생 시 알림

**Success Criteria:**
- [ ] `make full` 실행 후 텔레그램으로 차익 결과 수신
- [ ] 크롤링 오류 발생 시 오류 알림 수신

---

## Phase Execution Order

```
Phase 1 (새 소스)
  → Phase 2 (안정화) ← 독립 실행 가능
  → Phase 3 (성능)   ← Phase 1 완료 후 진행 권장
  → Phase 4 (알림)   ← Phase 2·3 완료 후 진행 권장
```

Phase 1과 Phase 2는 병렬 진행 가능.

---

*Roadmap created: 2026-04-13*
*Last updated: 2026-04-13 after GSD initialization*
