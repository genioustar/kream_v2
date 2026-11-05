# resell-sniper 검증 지도

터미널에서 크롤러를 직접 실행하는 경로만 적는다. 구현 세부는 적지 않는다.

## Baseline preconditions

- 작업 디렉터리는 `/Users/ny/Documents/programs/resell-sniper` 이다.
- 인터프리터는 `./venv/bin/python` 이다.
- Doctor가 통과한 뒤에만 기능을 돌린다.
- 파이프라인 프로세스는 동시에 하나만 둔다.
- 네이버 로그인·보안 화면이 나오면 열린 Chrome에서 확인한 뒤 터미널에 Enter를 넣는다. `q` 는 그 대기를 중단한다.

## Driving conventions

- 각 기능 파일의 명령을 그대로 실행한다.
- 사이트 HTML을 정적 도구로 받아 크롤링 결과를 대신하지 않는다.
- 증명은 명령, exit code, 로그, 그리고 만들어진 JSON 경로다.
- 증거는 `.cursor/skills/verify-resell-sniper/artifacts/<feature-id>/` 에 둔다. 정리할 때 지우지 않는다.

## Proof and skip reporting

- 도달하지 못한 단계는 실행한 명령과 막힌 이유(로그인 대기, 진단 JSON)를 적는다.
- 다른 모드의 성공으로 그 단계를 통과 처리하지 않는다.

## Features

- [Chrome 연결 확인](./chrome-check.md) 은 `python chrome.py` 로 전용 Chrome을 띄우고 `navigator.webdriver` 가 꺼져 있는지 본다.
- [사이트 수집](./crawl.md) 은 `python main.py --mode crawl` 로 오늘자 상품 JSON을 만든다.
- [전체 파이프라인](./full-pipeline.md) 은 `python main.py` 로 수집 뒤 Kream 차익을 검색한다.
- [Kream 차익 검색](./kream-only.md) 은 오늘자 상품 JSON이 있을 때 `python main.py --mode kream` 만 실행한다.
