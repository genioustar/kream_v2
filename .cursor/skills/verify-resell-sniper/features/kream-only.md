# Kream 차익 검색

`python main.py --mode kream` 은 오늘자 `*_products.json` 을 읽어 Kream 검색만 한다. 수집은 다시 하지 않는다.

## Sub-features

- `kream-requires-json` 은 오늘자 상품 JSON이 없으면 검색을 시작하지 않고 종료한다.
- `kream-search` 는 JSON이 있고 비교 대상이 있으면 차익 결과를 저장한다.
- `kream-skip-empty` 는 비교 대상이 없으면 검색을 생략한다.

## How to get to it (user POV)

- 오늘자 수집이 끝난 뒤 저장소 루트 터미널에서 `python main.py --mode kream` 을 실행한다.

## Driving it with the terminal

Preconditions:

- Doctor가 통과했다.
- 다른 파이프라인이 없다.
- 없음을 증명할 때가 아니면 `output/YYYYMMDD/*_products.json` 이 이미 있다.

- **입력 없음.** 오늘자 JSON을 잠시 다른 이름으로 옮긴 뒤 `./venv/bin/python main.py --mode kream` 을 실행하면 exit 0이 아니고 로그에 `*_products.json 파일이 없습니다` 가 있다. 확인 후 파일을 원래 이름으로 되돌린다. 오늘자 파일이 없으면 이 단계는 이미 그 상태이므로 옮기지 않는다.
- **검색.** JSON을 되돌린 뒤 같은 명령을 다시 실행한다. 비교 대상이 있으면 Kream 로그와 결과 파일이 생긴다. 없으면 생략 로그로 끝난다.
- **Proof.** 두 실행의 stdout, stderr, exit code를 `artifacts/kream-only/` 에 저장한다. 결과 파일 경로는 `outputs.txt` 에 적는다.

## Gotchas

- 오늘자 JSON을 지우면 복구할 수 없다. 증명을 위해 삭제하지 말고 이름만 바꾼 뒤 되돌린다.
- 이 모드는 사이트 수집을 대신하지 못한다. crawl 통과로 kream을 통과 처리하지 않는다.
- 라이브 Kream은 로그인·차단 화면에 멈출 수 있다. 그 로그를 생략 성공으로 적지 않는다.
