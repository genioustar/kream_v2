# 변경 히스토리

## 2026-11-11 — 무신사 대기 순서 테스트, SSF 하한 미달 로그

- [무신사] 브랜드 순회가 다음 주소 반복이나 페이지 상한으로 끊길 때, 그 페이지에서는 요청 간격을 기다리지 않는다. 테스트는 예외 전 `asyncio.sleep(REQUEST_DELAY_SEC)` 횟수가 실패 페이지보다 하나 적은지 확인하고, 상한 테스트는 상한보다 하나 적은 횟수를 확인한다.
- [SSF SHOP] 수집 수가 `MIN_PRODUCTS` 미만이면 `RuntimeError` 앞에 수집 수, 하한, 종료 페이지를 lazy %-format ERROR 로그로 남긴다.

## 2026-11-11 — Kream 상세의 죽은 서버 HTML 경로 제거

- [Kream] `_load_detail_model_number`는 상세 모델번호를 `attached` DOM 텍스트에서만 읽는다. `ego/space_host.mjs`는 응답 본문을 나이키 `api.nike.com/discover/product_wall` XHR에만 담고 Document `body`는 항상 null이라, `response.text()`는 `RuntimeError("응답 본문이 없습니다.")`로 실패한다. `logs/crawler_20261109.log`의 DOM 폴백 433회, `crawler_20261110.log` 517회, `crawler_20261111.log` 309회가 모두 `error=RuntimeError`였다.
  - SUPERSEDED: 상세 서버 HTML 을 우선 파싱하고 DOM 으로 폴백 — ego lite 는 Document 본문을 넘기지 않아 서버 경로가 항상 실패했다
- [Kream] 상세가 차단 화면이면 `_load_detail_model_number`는 `CrawlBlockedError`를 올리고 `wait_for_selector`와 locator 대기를 호출하지 않는다. `KreamDetailModelLoadingTests.setUp`의 `_is_blocked_page` 거짓 패치는 그대로 두고, 그 테스트 안에서만 참으로 덮어 확인한다.

## 2026-11-11 — SSF 최소 상품 수 하한(1,000)

- [SSF SHOP] 카드 0개 페이지로 순회가 끝난 뒤 수집 수가 `MIN_PRODUCTS`(1,000) 미만이면 `RuntimeError`를 올리고 결과를 반환하지 않는다. 2026-11-11 실측은 1~41페이지에 상품이 있고 42페이지에서 카드 0개로 끝나 2,184개(162초)였으며, 하한은 그 약 46%다. `SSFSHOP_SALE_URL`이 비어 있으면 하한 검사 없이 `[]`다.
  - SUPERSEDED: 목록 영역에 카드 0개면 그때까지의 결과를 정상 완료로 반환 — 중간 점검 화면이면 일부만 저장됐다

## 2026-11-11 — 공유 훅과 개인 Claude 설정 분리

- [하네스] 훅(PreToolUse 임시 경로 차단, PostToolUse 커밋 후 push)을 추적되는 `.claude/settings.json` 으로 옮겼다. 권한 전체 허용·`dangerouslySkipPermissions`·모델 같은 개인 설정은 `.claude/settings.local.json` 에만 두고 git 추적에서 빼 `.gitignore` 에 넣었다. 저장소가 공개라서 개인 권한 설정이 노출되지 않게 하되, Orca worktree 는 커밋된 파일로 만들어지므로 훅은 계속 worker 에 적용되게 했다.
  - SUPERSEDED: 훅과 개인 설정을 함께 `.claude/settings.local.json` 에 두고 추적 — 공개 저장소에 개인 권한 설정이 올라갔다. 과거 커밋에는 그 내용이 남아 있다.

## 2026-11-11 — GitHub Actions 로 전체 테스트 실행

- [CI] `.github/workflows/tests.yml`: main push 와 PR 에서 Python 3.14 로 `requirements.txt` 를 설치하고 로컬과 같은 `PYTHONPATH=. python -m unittest discover -s tests` 를 실행한다. 저장소 규칙 검사가 다른 기기나 에이전트의 push 에서도 돈다. 깨끗한 가상환경에서 커밋된 트리만으로 214개 통과를 먼저 확인했다.

## 2026-11-11 — 반복된 실수를 저장소 규칙 검사로 고정

- [하네스] `tests/test_rule_crawler_except.py`: 크롤러 모듈의 except 가 raise 없이 break 하거나 CrawlBlockedError 를 삼키면 실패. 실제 과거 코드(9d5690a 무신사 250·307행)를 잡는 것을 확인했다.
- [하네스] `tests/test_rule_docs_sync.py`: ARCHITECTURE.md tests 트리와 SITE_DISCOUNT_RATES 표를 실제 파일·config.py 와 대조. 추가 즉시 문서의 아디다스 30%(실제 20)와 규칙 테스트 2개 누락을 잡아 함께 고쳤다.
- [하네스] `tests/test_rule_logging.py`: f-string 로그 호출 수 래칫(AST 기준 127). 9d5690a(126) 이후 cdb3c09(129)에서 늘어난 것과 같은 증가를 막는다.
- CLAUDE.md 에 "규칙과 강제 수단" 표를 두었다.

## 2026-11-11 — scratchpad 밖 임시 경로 쓰기 차단 훅 추가

- [하네스] `.claude/hooks/block-tmp-writes.py` 를 `.claude/settings.local.json` 의 `PreToolUse`(Bash·Write·Edit·MultiEdit·NotebookEdit)에 등록했다. `/tmp`·`/private/tmp` 아래이면서 이 프로젝트 scratchpad 루트 밖인 경로에 쓰는 파일 도구 호출과 셸 쓰기(`>`, `>>`, `tee`, `touch`, `mkdir`, `cp`·`mv` 목적지, `-o`, `of=`, `open(..., 'w')`)를 막는다. 읽기와 정리용 `rm`, `/tmp` 에서 scratchpad 로 가져오는 복사는 허용한다.
- Grok CLI(Orca worker)도 같은 설정 파일의 훅을 읽는 것을 실험으로 확인했으므로, 훅 하나가 Claude 세션과 worker 양쪽에 적용된다. 계기는 잘못된 scratchpad 경로에 쓴 실수 2회와 worker 가 `/tmp` 에 남긴 파일들이다.
- 한계: 셸 명령은 문자열 휴리스틱이라 `cd /tmp && echo > x` 같은 상대 경로 쓰기와 `$TMPDIR` 은 잡지 못한다.

## 2026-11-11 — SSF 파서 오류 메시지에 실제 위치 표시

- [SSF SHOP] 목록 파서의 `RuntimeError`는 마지막으로 본 godNo 대신 오류가 난 자리를 메시지에 붙인다. 목록 영역 앞·뒤, 영역 안(첫 카드 전 또는 직전 카드), 카드 안(그 카드의 godNo)이다. 목록 영역이 없다는 메시지는 위치를 붙이지 않는다. 카드가 0개인 정상 종료 로그는 `목록 영역에 카드 0개`다.

## 2026-11-11 — 무신사 브랜드 순회가 끝나지 않는 경우를 예외로 막음

- [무신사] 브랜드 순회는 방문한 `nextPageUrl`을 기억한다. 같은 주소가 다시 나오면 `RuntimeError`로 중단한다. 첫 요청 주소도 방문 집합에 넣는다. `hasNext`가 참인 채 `MAX_BRAND_PAGES`(100, 페이지당 100개라 최대 1만 개)에 도달해도 `RuntimeError`로 중단한다. 두 경우 모두 그때까지 모은 상품을 반환하지 않는다. `hasNext`가 거짓이면 상한 페이지를 포함해 정상 종료한다.

## 2026-11-10 — SSF SHOP 아디다스 코리아 상품 크롤러 추가

- [SSF SHOP] `ssfshop/crawler.py`가 `config.SSFSHOP_SALE_URL`의 ADIDAS KOREA 브랜드관 목록을 URL 그대로 수집한다. 의류·잡화·신발 전체를 포함하고 신발만 걸러내지 않는다. 목록은 서버가 렌더한 HTML이라 브라우저 없이 urllib GET으로 읽고, `currentPage`는 1부터 덮어쓰며 `#goods_container`가 비면 끝낸다.
- 중간 페이지 실패와 `MAX_PAGES`(150) 도달은 일부 결과를 반환하지 않고 예외로 올린다. HTTP 403/429는 `CrawlBlockedError`다.
- 저장 가격은 카드의 판매가(추가할인이 이미 반영된 금액)다. `SITE_DISCOUNT_RATES["ssfshop"] = 0`이라 `sale_price`는 산출하지 않는다. 이름 끝 모델코드는 `suffix_single`로 뽑는다.
- 상품명에서 모델코드가 여러 개 나오면 이름 끝의 마지막 하나만 상품으로 만든다. `3STR` 같은 3-STRIPES 약어는 Kream 검색 대상이 되지 않는다.
- `main.py` STEP 1 무신사 블록 다음에 `ssfshop_products.json` 저장을 붙였다. diff와 Kream 적재는 기존 `*_products.json` 글롭을 그대로 쓴다.
- 목록 파서는 `/>` 없는 void 태그를 스택에 넣지 않는다. 카드나 `#goods_container`가 닫히지 않으면 일부 목록 대신 `RuntimeError`를 올린다.
- `#goods_container` 안에서는 태그 이름 스택으로 짝을 검사하고, 짝이 맞지 않거나 한 카드의 이름·가격 영역이 두 번 열리면 일부 목록 대신 `RuntimeError`를 올린다.
- 컨테이너가 없는 응답, 두 번 열린 컨테이너, 컨테이너 안의 section, 짝 없는 section 끝은 일부 목록 대신 `RuntimeError`다. 컨테이너가 있고 카드가 0개일 때만 정상 종료다.

## 2026-11-09 — 무신사 수집 도중 실패 시 부분 결과를 저장하지 않음

- [무신사] 페이지 순회 도중 요청이 실패하면 예외를 전파한다. 403/429 는 CrawlBlockedError 로 파이프라인을 멈추고, 그 외 실패는 main 이 경고 후 파일을 저장하지 않아 다음 실행에서 재시도된다.
  - SUPERSEDED: 요청 실패 시 break 로 빠져나가 그때까지 모은 일부를 정상 결과로 반환 — 부분 결과가 musinsa_products.json 으로 저장되어 같은 날 재실행에서도 건너뛰었다.
- [무신사] 브랜드 응답이 `hasNext`인데 `nextPageUrl`이 비어 있으면 `RuntimeError`로 중단한다. 요청 실패 로그에 예외 내용을 남긴다.
- [무신사] 페이지 사이 요청 간격(마지막 페이지 뒤에는 쉬지 않음)을 테스트로 고정.

## 2026-11-09 — 검증용 브라우저를 ego-browser 로 통일, 브라우저 에러 규칙 추가

- [검증] 크롤러 DOM 교차 검증과 브라우저 에러 진단은 `/ego-browser` 로 한다. `.claude/rules/browser-errors.md` 가 에러 종류별 조치와 "알려진 에러 원장"을 세션마다 자동으로 불러와 같은 시도를 반복하지 않게 한다.
  - SUPERSEDED: CLAUDE.md 의 "크롤러 검증 시 Playwright MCP 활용 규칙"(MCP 설치 절차 포함) — 크롤러가 이미 ego lite 로 바뀌었고 검증 브라우저도 같은 계열로 통일했다. ARCHITECTURE.md 검증 문구도 함께 고쳤다.

## 2026-11-06 — Kream 상세의 짧은 화면은 전체 검색을 끊지 않음

- [Kream] 상세 본문을 읽지 못하거나 500자 미만이어도 차단으로 보지 않는다. `잠시 후 다시 시도` 같은 차단 문구와 HTTP 403/429만 이후 검색을 멈춘다.
  - SUPERSEDED: 본문 읽기 실패와 짧은 HTML을 접근 제한으로 보고 청크를 중단 — 상품 페이지로 넘어가는 순간에 한 번 실패해도 남은 모델 검색이 같이 끊겼다.

## 2026-11-06 — 네이버 상품 URL을 스토어 origin 기준으로 생성

- [네이버 URL] 상대 경로 상품 URL이 `clean_url`의 기본 base `https://www.naver.com`에 붙어 `www.naver.com/{스토어}/products/{id}`로 저장되었다. `page_origin(page_url)`을 추가하고 `clean_url`의 `base`를 필수 인자로 바꿨다. 카드별 파싱(`_extract_product`)과 일괄 읽기(`_product_from_card`)는 `_collect_products`가 `page.url`에서 만든 origin을 같은 인자로 받는다.
  - SUPERSEDED: `clean_url`이 `base="https://www.naver.com"`을 기본값으로 두고 크롤러가 base 없이 호출하던 방식 — 스토어 호스트가 상품 URL에 들어가지 않았다.
- 수정 후 첫 실행은 URL 변경으로 전체 상품이 diff 에서 modified 로 잡혀 Kream 전체 검색이 일어난다. 11월 10일(전체 검색일) 실행 전에 병합하면 추가 비용이 없다.
- [네이버 체크포인트] 체크포인트 `version` 을 1→2 로 올리고 상수 `CHECKPOINT_VERSION` 으로 뺐다. 수정 전 `www.naver.com` URL 이 담긴 같은 날 체크포인트가 병합 당일 재실행에서 재사용되는 것을 막는다.
  - SUPERSEDED: 체크포인트 version 1 하드코딩 3곳 — URL 생성 규칙이 identity 에 없어 규칙이 바뀌어도 옛 캐시가 유효했음
- 체크포인트가 이미 있는 날 병합하면, 그날 첫 실행에서 네이버 8개 스토어를 모두 다시 수집한다(브라우저 실행, 접근 제한 가능성). 다음 날부터는 날짜 경로가 달라 영향이 없다. 옛 version 1 파일은 아무 코드도 읽지 않으므로 지우지 않아도 된다.
- `--mode kream` 은 오늘자 `*_products.json` 을 그대로 읽으므로 이 수정의 보호를 받지 않는다. 병합 당일에는 `full` 또는 `crawl` 로 결과 파일을 다시 만든 뒤 사용한다.

## 2026-11-06 — 세일 URL 비활성 설정 정리와 테스트 기준선 복구

- [설정] 나이키·아디다스·special 세일 URL을 빈 문자열로 정의해 비활성으로 유지하고, 세 크롤러는 `config.X_SALE_URL`을 직접 읽는다. 세일이 열리면 값만 채워 다시 켠다. 테스트 기준선(106개 전체 통과)을 복구하는 과정에서 정해졌다.
  - SUPERSEDED: 세일 URL을 주석 처리해 속성 자체를 없애고 크롤러가 `getattr` 기본값으로 읽던 방식 — 속성이 없으면 테스트가 `AttributeError`로 깨지고 설정 존재 여부가 코드에서 드러나지 않아, 빈 문자열 정의와 직접 접근으로 되돌림
- [테스트] 아디다스 HTTP 테스트는 테스트 URL을 주입하고, 네이버 수집 테스트는 일괄 카드 읽기가 실패하면 카드별 조회로 가도록 fixture를 맞춘다. 일괄 읽기 성공 경로를 검증하는 테스트는 아직 없다.

## 2026-11-05 — Kream 차익 결과에 직전 날짜 대비 표시 추가

- [Kream] `kream/history_marker.py` 신설. 저장 시 오늘 이전 가장 최신 `output/YYYYMMDD/arbitrage_results.json`과 `(model_name, marketplace_site)` 키로 비교해 `previously_saved`, `price_status`(new/same/changed), changed일 때 `previous_marketplace_price`를 항목에 추가한다. `marketplace_price`만 비교하며 이전 파일은 청크 루프 전 1회만 읽는다. 같은 키의 여러 가격은 집합으로 보관해 오늘 가격이 집합에 있으면 same, 없으면 changed(이전 가격은 차이가 가장 작은 값, 동률이면 더 작은 값)로 판정한다. 이전 파일 없음은 전부 신규, JSON 오류·최상위 list 아님은 warning 후 전부 신규, 손상 레코드(dict 아님·키 누락·model_name/marketplace_site 비문자열·가격 None/bool/쉼표 문자열/비정수 float/Infinity/NaN)는 해당 건만 건너뛰고 건수를 warning으로 남긴다. 정수형 float(100.0)과 숫자 문자열("129000")은 허용한다.
- [Kream] `main.py`의 결과 파일명을 `history_marker.RESULT_FILENAME` 상수로 통일.
- [테스트] `tests/test_history_marker.py` 추가.

## 2026-11-03 — Kream 검색을 ego lite 페이지 호출로 맞춤

- [Kream] 검색 버튼이 보이는지 확인하고, 검색어는 키보드로 넣고, 상세의 `모델번호` 문단은 렌더된 텍스트에서 읽는다. 클릭을 가로막는 요소가 있으면 DOM click으로 연다.

## 2026-11-03 — ego lite는 뒤에서만 동작

- [브라우저] `open -a "ego lite"`를 빼서 크롤이 앱 창을 앞으로 가져오지 않는다.
  - SUPERSEDED: 크롤 시작 시 앱 창을 앞으로 염 — 사용자는 뒤에서 도는 쪽을 선택했다.
- [루티크] 검색 버튼은 다른 요소가 클릭을 가로막는다. 그 경우에는 버튼의 DOM click으로 검색창을 연다.

## 2026-11-03 — ego lite 창을 앞으로 염

- [브라우저] 크롤 시작 시 `open -a "ego lite"`로 이미 켜진 앱 창을 앞으로 연다. `ego-browser`는 그 앱 안의 Space에 붙고, 새 브라우저 프로세스를 만들지 않는다.

## 2026-11-03 — ego lite 소켓 수신의 UTF-8 조각

- [브라우저] Task Space 응답을 개행 단위로만 UTF-8 디코드한다. 한글이 패킷 경계에서 갈라져도 수신 스레드가 죽지 않는다.

## 2026-11-03 — 크롤링을 ego lite Task Space 로 옮김

- [브라우저] 크롤러는 ego lite 를 따로 띄우지 않는다. `ego-browser` 로 사용자의 ego lite 안에 Task Space `resell-sniper` 를 열고, 끝나면 그 Space 만 닫는다. 기본 프로필의 로그인을 쓰고 다른 탭은 건드리지 않는다.
  - SUPERSEDED: ego lite 를 `--remote-debugging-port` 와 `output/session/ego-profile` 로 새로 띄우고 전체 화면으로 키움 — 그 방식은 별도 브라우저 창이라 ego lite 의 Space 격리와 로그인 공유를 쓰지 않는다.

## 2026-11-03 — 크롤링 브라우저를 ego lite로 교체

- [브라우저] `chrome.py`는 같은 CDP 플래그로 ego lite를 띄운다. 실행 파일은 `/Applications/ego lite.app/Contents/MacOS/ego lite`이고 프로필은 `output/session/ego-profile`이다. `--enable-automation`은 넣지 않는다. ego lite는 연결 직후 페이지가 없고 일반 크기 지정을 1x1로 무시하므로, 작업 탭을 연 뒤 전체 화면으로 맞춘다.
  - SUPERSEDED: macOS 실행 파일을 Google Chrome으로 두고 `output/session/chrome-profile`을 씀 — 설치된 ego lite가 같은 디버깅 포트로 DevTools를 연다.

## 2026-11-02 — 크롤링 Chrome 창을 작업 영역에 맞춤

- [브라우저] 크롤링에 쓰는 Chrome은 실행 시 `--start-maximized`로 띄우고, 연결 직후 화면 작업 영역(`screen.avail*`) 크기로 맞춘다. 이미 떠 있는 전용 창도 같은 크기로 키운다.

## 2026-10-30 — 네이버 로그인 화면에서 대기

- [네이버] 로그인 페이지로 떨어진 문서 응답도 크롤을 끊지 않는다. `loutique` 홈이 `nidlogin.login`으로 302/307되면 Enter를 기다린 뒤 같은 공개 검색을 다시 연다.
  - SUPERSEDED: 로그인 문서 응답을 `_set_block`으로 즉시 중단 — 화면 판정만 대기로 바꾸고 응답 경로는 그대로 중단하고 있었다.

## 2026-10-30 — 자동화 표시 없는 Chrome 연결

- [브라우저] 크롤러는 Playwright가 Chrome을 직접 실행하지 않고, 전용 프로필의 일반 Chrome에 CDP로 붙는다. `--enable-automation`은 넣지 않고 `navigator.webdriver`만 숨긴다. UA·언어·플랫폼·WebGL은 실제 Chrome 값을 유지한다. 순차 실행, 작업 탭 1개, 차단 시 재시도 없음은 유지한다.
  - SUPERSEDED: Playwright `chromium.launch(channel="chrome")`로 독립 창 실행 — 자동화 플래그 때문에 브라우저가 차단되었다.
  - SUPERSEDED: 디버깅 포트와 stealth를 쓰지 않음 — 참고 저장소의 일반 Chrome 연결로 되돌리되, 임의 UA·Win32 플랫폼 위조와 쿠키 JSON 복원은 넣지 않는다.

## 2026-10-28 — 네이버 상품 주소의 기준 호스트

- [네이버 URL] `clean_url`은 카드를 연 페이지의 origin을 `base`로 받는다. 상대 경로가 `www.naver.com`으로 붙거나 인자 누락으로 파싱 오류가 되지 않는다.

## 2026-10-08 — 루티크 공개 검색 UI 경로

- [루티크 검색] 검색 URL 직접 접속은 HTTP 307으로 로그인 화면에 이동하지만 공개 스토어에서 검색창→검색→80개 보기를 사용하면 인증 쿠키 없이 동일한 디젤 검색 55개 카드가 제공됨을 Playwright MCP로 확인했다. `navigation="store_search_ui"`로 해당 UI 경로를 사용하며 검색 조건·결과 완전성을 검증한다.
  - SUPERSEDED: 모든 네이버 설정 URL을 `goto`로 직접 이동 — 루티크 검색의 직접 진입과 공개 UI 검색 동작이 달라 로그인 유도가 발생했다.
- [수집 무결성] 실제 카드 파싱 오류가 있으면 완료 캐시를 만들지 않으며 진단에 제외 사유별 수를 남긴다. 품절·모델명 없음의 정상 제외는 유지한다.

## 2026-10-07 — 네이버 보호조치 진단·비로그인 수집

- [네이버 진단] 보호조치·로그인 요구·보안 확인·관련 HTTP 제한·목록 누락·이동 오류를 구분하여 스토어별 관측 보고서를 저장한다. URL 쿼리와 인증정보를 제외하며, 제한 신호가 생기면 현재 작업과 후속 스토어를 중단한다. 네이버 내부의 보호조치 판정 사유까지 확정하지는 않는다.
  - SUPERSEDED: 목록이 15초 내 나타나지 않으면 로그인·보안 확인 후 Enter로 재개 — 일반 로딩 오류와 계정 문제를 혼동하고 개인 계정 로그인을 유도하므로 수동 입력 루프를 제거했다.
- [네이버 완료 캐시] 같은 날짜·URL·스토어 타입·모델 추출 전략의 완료 결과만 재사용한다. 중간 실패 시 완료 스토어 재방문을 줄이고 미완료 결과가 최종 상품 파일로 오인되지 않도록 별도 경로에 원자적으로 저장한다.

## 2026-10-06 — 아디다스 아울렛 상품 목록 URL 갱신

- [아디다스 URL] 공식 아울렛의 전체보기 링크 `/outlet?grid=true`로 설정을 갱신했다. `/outlet`은 캠페인 화면이며 `grid=true`가 상품 목록을 연다. 전체 카테고리 수집과 Kids 제외 조건을 유지한다.
  - SUPERSEDED: `/big_discount` 할인전 URL 사용 — 현재 실제 브라우저에서 404를 반환한다. 오늘 실행 로그에는 별도로 Akamai 메인 문서 403도 확인되어 필수 응답 차단 시 중단 정책은 유지한다.
- [검증] 전체 64개 테스트 통과. 새 URL에서 44페이지를 끝까지 수집해 Kids·중복 제외 1,796개 저장. 종료 후 독립 Playwright MCP 무작위 3개 모델·가격 정확 일치, 상품명은 기존 슬러그 정규화 계약 기준 일치로 PASS (`output/20261006/validation/adidas_validation.json`).

## 2026-09-29 — 아디다스 HTTP 오류 진단·제한된 재시도

- [아디다스] 문서 및 XHR/fetch HTTP 오류를 기록하고 실패 시 상태·본문 일부·화면을 저장한다. 접근·렌더링·페이지 진행 실패는 빈 결과와 구분해 예외로 전달하며 부분 수집 결과를 저장하지 않는다.
  - SUPERSEDED: goto 오류를 무시하고 카드 탐색 실패를 빈 목록으로 반환 — HTTP 오류가 상품 0개로 오인되는 문제를 수정했다.
- [429] 최초 문서에 한해 Retry-After를 존중해 최대 2회 재시도한다. 헤더가 없거나 잘못되면 5·10초 대기, 서버 요구 대기가 60초를 넘으면 조기 재시도 없이 중단한다. 403은 자동 재시도하지 않는다.
  - SUPERSEDED: 모든 크롤러의 접근 제한에 일률적으로 자동 재시도 금지 — 아디다스 최초 문서의 429만 유한 재시도를 허용하며 다른 크롤러 정책은 유지한다.
- [실측] `/big_discount`는 HTTP 200과 407개 목록을 제공한다. 개별 상품 보조 API `/plp-app/api/product/{code}` 및 장바구니 요청은 Akamai 403이 발생해도 목록이 렌더될 수 있어 진단만 남기며, 필수 목록 응답 실패와 구분한다. URL·공통 브라우저·프로필은 유지한다.
- [검증] 전체 64개 테스트 통과. 실제 9페이지에서 Kids 제외 371개 수집, 종료 후 독립 Playwright MCP의 무작위 3개 상품명·모델코드·가격 대조 PASS (`output/20260929/validation/adidas_validation.json`).

## 2026-09-25 — 독립 Chrome 창 복원·추가 휴식 제거

- [브라우저] 확장 없이 Python Playwright가 독립 Chrome 창을 실행·종료한다. 단일 작업 탭 제한은 유지하며 기존 Chrome 로그인은 공유하지 않는다. Node 브리지·의존성·전용 테스트를 제거했다.
  - SUPERSEDED: 2026-09-15 확장으로 기존 Chrome 연결 — 사용자 요청으로 별도 창 실행으로 복원했다.
- [대기] 네이버 스토어 전환 1~3초, Kream 추가 휴식 없음. 렌더링/입력 대기와 접근 제한 중단은 유지한다.
  - SUPERSEDED: 스토어 30~60초, Kream 검색 20~40초·상세 10~20초·20건마다 120~180초 및 청크 간 30분 휴식 — 실행 속도 변경 요청에 따라 제거했다.

## 2026-09-18 — 단일 작업 탭·순차 처리·요청 간 휴식

- [동시성] 네이버 스토어와 Kream 모델을 순차 처리하고 공통 탭 팩토리에서 작업 탭 1개를 강제한다. 브리지의 연결용 빈 탭도 제거했다.
  - SUPERSEDED: 네이버 전체 스토어 병렬 실행·Kream 2개 페이지 풀·연결용 별도 탭 — 한꺼번에 여러 탭이 열리고 요청이 겹치는 문제로 순차 처리로 전환했다.
- [휴식] 스토어 간 30~60초, Kream 검색 전 20~40초·상세 전 10~20초, 20건마다 120~180초 휴식. 목록 스크롤과 API 페이지 간격도 늘렸다.
- [접근 제한] 네이버 문서/Kream 문서·API의 403/429 및 Kream 차단·입력 실패·카드 타임아웃은 후속 요청을 중단한다. Kream 완료 모델의 차익 결과는 저장한다.
  - SUPERSEDED: 차단 후 30·60·120초 재시도 및 홈 재이동 — 접근 제한 중 추가 요청을 방지하도록 자동 재시도를 제거했다.
- [불필요 요청] 변경 상품이 없는 diff는 전체 검색으로 폴백하지 않고 종료한다.


## 2026-09-15 — 일반 Chrome 확장 연결로 전환

- [Chrome 연결] 공식 Playwright Extension과 자동 실행 Node 브리지를 통해 평소 Chrome 프로필에서 새 탭을 생성한다. 브라우저 기반 5개 크롤러 모두 공통 연결을 사용하며 `chrome.py`는 선택적 연결 점검으로 변경했다.
  - SUPERSEDED: 9222 CDP Chrome 사전 실행·별도 프로필 재사용 — 사용자가 요청한 일반 Chrome 창을 그대로 사용하기 위해 디버깅 포트 의존성을 제거했다.
  - SUPERSEDED: 별도 Playwright 브라우저·UA/Client Hints 생성·stealth 주입·쿠키 JSON 복원 — 기존 프로필 상태를 사용하며 관련 잔재는 제거했다.
- [탭 소유권] Python이 만든 작업 탭만 정리하고 사용자 탭은 보존한다. Node의 별도 연결 탭은 2026-09-18 제거했다.
- [의존성] Python Playwright와 Node playwright-core를 1.62.0으로 고정한다. 첫 확장 설치/연결 승인이 필요하며 선택적으로 로컬 환경의 확장 토큰을 사용할 수 있다.

## 2026-08-05 — Kream 검색 결과 상세 모델번호 검증

- [Kream 모델 일치] 검색 카드의 모델을 검색어로 간주하지 않고 각 후보 상세 페이지의 `모델번호`를 읽어 정규화된 전체 문자열이 일치할 때만 Kream 상품과 차익 결과에 포함한다. 상세 모델번호 누락·파싱 실패·불일치는 fail-closed로 제외하며 comparator에서도 저장 전 다시 검증한다.
  - SUPERSEDED: 검색 결과가 1~2개면 첫 번째 상품을 정확 매치로 간주하고 3개 이상이면 전부 제외 — Kream 유사 검색이 `IG5749` 검색에 실제 `IG5349` 상품을 첫 번째로 노출할 수 있어, 상위 5개 후보의 상세 모델번호 완전 일치 검증으로 교체했다.

## 2026-07-29 — 네이버 상품 건너뜀 사유 로그 세분화

- [네이버 크롤링 로그] 사이트별 파싱 완료 로그의 `건너뜀`을 `품절`·`모델명 없음`·`파싱 오류`로 나눠 집계한다. 기존 총 건너뜀 수도 유지해 수집 제외와 실제 파싱 실패를 즉시 구분할 수 있게 했다.

## 2026-07-28 — 사이트별 모델명 추출 전략 통합

- [모델명 추출] `common/model_names.py`를 공통 추출기로 추가하고 괄호·슬래시·후행·본문 코드, 숫자-only 코드, 공백 복합 SKU, 복수 대체 모델을 사이트별 전략(`bracket_first`, `slash_first`, `suffix_single`, `suffix_grouped`, `auto`)에 따라 처리한다. 시즌·사이즈·수량·용량 표기는 공통 오탐 필터로 제외한다.
  - SUPERSEDED: 무신사 모델명을 대문자+숫자 스타일코드의 마지막 값만으로 추출 — 실제 아울렛 상품의 괄호 숫자 코드, 슬래시 코드, 후행 코드, 목록용 보조번호 표기를 누락해 공통 `slash_first` 전략으로 교체했다.
- [네이버·special 연동] `config.SEARCH_URLS[*].model_strategy`와 `SPECIAL_MODEL_STRATEGY`를 추가하고, 네이버 및 special 크롤러가 추출된 복수 모델을 각각 `MarketplaceProduct`로 저장하도록 통일했다.
- [무신사 URL] `/brand/{slug}` PLP와 현재 설정된 `/main/.../ranking`을 모두 지원하며, 랭킹 API의 `link.next`를 따라 수집한다.
  - SUPERSEDED: 무신사 크롤러가 브랜드 PLP URL만 지원 — 실제 설정 URL이 아울렛 랭킹으로 바뀌어 URL 형식별 API 분기로 확장했다.
- [검증] 실제 스토어 표기의 모델코드와 `230~250MM`·`2way` 오탐 방지를 `tests/test_model_names.py`에 고정했다.

## 2026-06-24 — smart_store 품절 상품 Kream 조회 제외

- `naver/crawler.py`: `smart_store` 상품 카드에서 `품절` 오버레이 또는 `일시품절` 배송 문구를 감지하면 수집하지 않도록 `_is_sold_out_product()` 추가.
- 품절 상품은 `marketplace_products.json`에 저장되지 않으므로 Kream 검색 대상에서도 자동 제외된다. `brand_store` 타입은 기존 동작을 유지한다.

## 2026-06-20 — 무신사 브랜드 PLP API 크롤러 추가

- `musinsa/crawler.py` 신규: `config.MUSINSA_SALE_URL`에서 brand slug와 query를 추출해 `api2/dp/v2/plp/goods`를 호출하고, `pagination.nextPageUrl`의 `hmacId`를 따라 전체 페이지를 수집한다.
- 무신사 가격은 DOM 카드에 표시되는 `finalPrice`를 우선 사용하고, 모델명은 대문자+숫자 스타일코드 패턴의 마지막 값을 우선 추출한다. 품절 상품과 모델코드 미추출 상품은 제외한다.
- `config.py`: 무신사 아디다스 무진장 캠페인 URL과 `SITE_DISCOUNT_RATES["musinsa"] = 0` 추가.
- `main.py`: STEP 1에 무신사 크롤링 블록 추가 (`musinsa_products.json` 저장). diff/Kream 검색은 기존 `*_products.json` 자동 로드 경로를 재사용한다.

## 2026-05-08 — 비교 맥락 식별자 naver → marketplace 일괄 변경

- `common/models.py`: `NaverProduct` → `MarketplaceProduct` (클래스명), `ArbitrageResult` 필드 `naver_site/price/url` → `marketplace_site/price/url`
- `kream/comparator.py`: 파라미터·루프 변수·ArbitrageResult 키워드 인자 전체 marketplace 로 통일
- `main.py`: import, 변수명(`naver_output/products`→`marketplace_output/products`), 파일명(`marketplace_products.json`), `_apply_sale_price` 두 번째 인자(`"marketplace"`)
- `config.py`: `SITE_DISCOUNT_RATES["naver"]` → `["marketplace"]`
- `diff_output.py`: `KEY_CONFIG` 키 `naver_products.json` → `marketplace_products.json`
- `kream/crawler.py`, `adidas/crawler.py`, `nike/crawler.py`, `special/crawler.py`: `NaverProduct` → `MarketplaceProduct`
- `special/crawler.py`: 공통 상품 타입을 `MarketplaceProduct`로 통일하고, 모델명 추출은 현재 `common.model_names`를 사용한다.
- `naver/` 디렉터리는 실제 네이버 크롤러이므로 유지하고 공통 데이터 타입·필드명만 `marketplace` 문맥으로 변경했다.

## 2026-05-07 — sale_price 도입 — 사이트별 수동 할인율 적용 후 Kream 차익 비교에 effective price 사용

- `config.py`: `SITE_DISCOUNT_RATES` 딕셔너리 추가 (naver·adidas·nike·special 각 0%). 0이면 sale_price 미산출.
- `common/models.py`: `NaverProduct`에 `sale_price: Optional[int] = None` 필드 추가. default=None으로 과거 JSON 호환 보장.
- `main.py`: `_apply_sale_price(products, source_key)` 헬퍼 추가. STEP 1 각 크롤러 호출 직후 저장 직전 호출. naver 9개 스토어 모두 단일 "naver" 할인율 적용.
- `kream/comparator.py`: `_effective_price(p)` 헬퍼 추가. `find_arbitrage`에서 naver.price 대신 effective price 사용. ArbitrageResult.naver_price·price_diff 모두 effective price 기준으로 산출.

## 2026-05-04 — SPECIAL_SALE_URL 범용 크롤러 추가 및 Kream 검색 연동 수정

- `special/crawler.py` 신규: `config.SPECIAL_SALE_URL`에 지정된 임의 쇼핑 페이지를 CDP로 크롤링. JS 휴리스틱(li → a → leaf 조상 3단계 전략)으로 상품 카드 자동 탐지, scrollY 변화 없음 5회 연속 시 종료.
- `config.py`: `SPECIAL_SALE_URL = ""` 추가. 빈 값이면 크롤링 스킵.
- `main.py`: STEP 1에 special 크롤링 블록 추가 (`special_products.json` 저장).
- `diff_output.py` 버그 수정: `diff_date_pair`가 새 날짜에만 존재하는 `*_products.json`을 건너뛰어 Kream 검색에서 누락되는 문제 → 신규 파일의 전체 항목을 `added`로 처리해 diff 파일 생성하도록 수정.

## 2026-04-22 — 문서·코드 정합성 정리 (하네스 엔지니어링 룰 도입)

### .planning/STATE.md — Decisions 갱신
- "CDP 대신 create_browser+new_stealth_page 사용" 결정을 SUPERSEDED 처리하고 CDP 채택 결정으로 대체

### CLAUDE.md — 하네스 엔지니어링 룰 추가
- 경로 교체 시 잔재 즉시 제거, grep 0회면 삭제, 문서/코드 1:1 정합성, SUPERSEDED 포맷, 세션 종료 전 체크리스트 명문화

### CLAUDE.md — 크롤러 검증 시 Playwright MCP 활용 규칙 추가
- Evaluator 단계에서 Playwright MCP 로 대상 사이트 DOM 과 크롤링 JSON 을 교차 검증하는 절차 도입
- Firecrawl·Fetch 정적 HTML MCP 사용 금지 (SPA·Kasada·Akamai 우회 불가)
- 검증은 CDP 포트 9222 세션을 크롤러와 순차 공유, 수정된 크롤러만 대상으로 수행
- 판정 기준: PASS(모든 항목 충족) / FLAG(상품수 미달이지만 샘플 일치) / FAIL(샘플 불일치·DOM 접근 실패)
- 설치 명령: `claude mcp add playwright npx @playwright/mcp@latest`

---

## 2026-04-15 — 나이키 크롤러 Kasada 우회 및 API 인터셉트 방식 전환

### 문제 진단
- `nike/crawler.py` 가 21개 상품만 수집하는 문제 발생
- 원인: Nike가 Kasada 봇 탐지를 사용하며, Playwright 자체 Chromium을 감지해 무한스크롤 JS를 비활성화 — 초기 SSR 21개만 서빙
- 증거: 크롤러 실행 시 scrollY가 0→4569px까지 정상 이동하지만 API 호출 전혀 없음

### 해결 방법
- **Playwright Chromium → 실제 Chrome CDP 전환**: `create_browser()` 제거, `async_playwright().connect_over_cdp("http://localhost:9222")` 채택 (아디다스와 동일 방식)
- **DOM 파싱 → API 응답 인터셉트**: `page.on("response", ...)` 로 `api.nike.com/discover/product_wall` 응답을 인터셉트해 JSON 직접 파싱
- **실제 API 응답 구조 확인**: `productGroupings[*].products` (기존 가정 `productWall.products` 오류)
- **올바른 필드명 적용**: `copy.title`, `copy.subTitle`, `prices.currentPrice`, `pdpUrl.url`, `productCode`
- **Makefile 수정**: `make nike` 타겟이 `make chrome`에 의존하도록 추가

---

## 2026-04-13 — Kream 검색 버튼 폴백 + 새 창 방지

### kream/crawler.py
- `_click_search_button()`: 단일 셀렉터(`KREAM_SELECTORS["search_button"]`) → `SEARCH_BUTTON_SELECTORS` 폴백 리스트 순서 시도 (긴 CSS 경로 매칭 실패 시 fallback)
- SUPERSEDED (2026-09-18): `search_kream()`의 차단 후 홈 재이동 재시도 — 접근 제한 중 추가 요청을 막기 위해 자동 중단으로 교체했다.
  - 새 창 무한 생성 문제 해결
  - 원본 페이지가 항상 pool로 복귀되어 pool 관리 정합성 확보

---

## 2026-04-13 — kream 모드 diff 적용

### main.py
- `kream` 모드에서도 diff가 있으면 diff 기반 모델명 사용 (기존: 항상 전체 모델명 사용)
- `is_full_crawl_day or mode == "kream"` 조건에서 `mode == "kream"` 제거
- diff 없으면 기존과 동일하게 전체 모델명으로 폴백

---

## 2026-04-11 — Kream 두 번째 검색 실패 수정

### kream/crawler.py
- `_open_search_and_get_input()`: 검색 결과 페이지에서는 입력창이 이미 열려있어 버튼 클릭 시 닫혀버림  
  → 입력창 존재 여부 먼저 확인 후 없을 때만 버튼 클릭

---

## 2026-04-09 — Kream SPA 라우팅 전환 & 탐지 우회 강화

### kream/crawler.py
- Kream은 Vue SPA: `page.goto(search_url)` 시 HTML 39 bytes로 미렌더링  
  → `page.evaluate("window.location.href = url")`로 SPA 내부 라우팅 트리거
- SUPERSEDED (2026-08-05): 검색 결과 선택을 거래량 최다에서 첫 번째 상품(검색 결과 맨 왼쪽)으로 변경 — 유사 검색 오탐 때문에 상세 모델번호 완전 일치 검증으로 교체했다.
- SUPERSEDED (2026-09-18): 검색 간 8~15초 대기·차단 후 지수 백오프 — 검색 전 20~40초 및 정기 휴식·접근 제한 중단으로 교체했다.

### common/browser.py
- SUPERSEDED (2026-09-14): UA·Client Hints 생성 및 쿠키 JSON 영속화 — 기존 Chrome 프로필 재사용으로 교체하고 관련 코드를 제거했다.

### naver/parser.py
- `price` 셀렉터를 fallback 후보 리스트로 변경 (DOM 구조 변경 시 자동 전환)
