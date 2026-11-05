# 사이트 수집

`python main.py --mode crawl` 은 설정된 사이트를 순서대로 수집하고 `output/YYYYMMDD/*_products.json` 을 남긴다. Kream 검색은 하지 않는다.

## Sub-features

- `crawl-start` 는 전용 Chrome으로 수집을 시작한다.
- `crawl-login-wait` 는 네이버 로그인·보안 화면에서 종료하지 않고 Enter를 기다린다.
- `crawl-save` 는 오늘 날짜 디렉터리에 사이트별 상품 JSON을 쓴다.

## How to get to it (user POV)

- 저장소 루트 터미널에서 `python main.py --mode crawl` 을 실행한다.

## Driving it with the terminal

Preconditions:

- Doctor가 통과했다.
- 다른 파이프라인이 없다.
- 오늘 날짜는 로컬 날짜 `YYYYMMDD` 다.

- **수집.** `./venv/bin/python main.py --mode crawl` 을 실행한다. 로그에 지금 수집 중인 사이트 이름이 나온다.
- **로그인 대기.** 로그가 로그인 또는 보안 확인을 알리고 입력을 기다리면, 열린 Chrome에서 확인한 뒤 Enter를 넣는다. `q` 를 넣으면 그 대기가 끝나고 네이버 수집이 중단된다. 에이전트가 로그인을 끝낼 수 없으면 대기 중인 로그와 진단 JSON 경로를 증거로 남기고 여기서 멈춘다.
- **저장 확인.** exit 0 이면 `output/YYYYMMDD/` 아래 `*_products.json` 이 있다. 경로와 상품 건수를 `artifacts/crawl/outputs.txt` 에 적는다.
- **Proof.** stdout, stderr, exit code를 `artifacts/crawl/` 에 저장한다.

## Gotchas

- 계정 보호와 수집 요청의 403/429는 이후 스토어를 돌리지 않고 종료한다. 이 종료를 로그인 대기와 같은 성공으로 적지 않는다.
- 상품 목록 타임아웃인데 로그인·보안 화면이 아니면 그 스토어는 실패다.
- 전체 사이트 수집은 수십 분이 걸릴 수 있다. 타임아웃으로 잘라 부분 로그만 통과로 적지 않는다.
