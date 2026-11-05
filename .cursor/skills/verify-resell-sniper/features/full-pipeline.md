# 전체 파이프라인

`python main.py` 는 사이트 수집 뒤 Kream에서 차익을 검색한다. 1일·10일·20일·30일이 아닌 날에는 변경분 모델을 쓰고, 변경분이 없으면 Kream 검색을 생략한다.

## Sub-features

- `full-crawl` 은 crawl과 같은 수집을 먼저 끝낸다.
- `full-kream` 은 비교 대상 모델이 있을 때 Kream 검색 결과를 저장한다.
- `full-skip-empty` 는 변경분이 없으면 Kream 검색 없이 끝난다.

## How to get to it (user POV)

- 저장소 루트 터미널에서 `python main.py` 를 실행한다.

## Driving it with the terminal

Preconditions:

- Doctor가 통과했다.
- 다른 파이프라인이 없다.

- **실행.** `./venv/bin/python main.py` 를 실행한다. 수집 로그가 먼저 나오고, 비교 대상이 있으면 Kream 검색 로그가 이어진다.
- **로그인 대기.** crawl 기능과 같다. 로그인·보안 화면이면 Chrome에서 확인한 뒤 Enter다.
- **결과.** exit 0 이다. 변경분이 없으면 로그에 Kream 검색 생략이 남고 차익 파일이 새로 생기지 않아도 된다. 비교를 수행했으면 오늘자 차익 결과 경로를 `artifacts/full-pipeline/outputs.txt` 에 적는다.
- **Proof.** stdout, stderr, exit code를 `artifacts/full-pipeline/` 에 저장한다.

## Gotchas

- 조용한 날의 생략은 실패가 아니다. 생략 로그 없이 차익 파일만 없다고 실패로 적지 않는다.
- Kream 차단이나 검색 실패는 완료분 저장 후 종료할 수 있다. exit 0이 아니면 통과로 적지 않는다.
- crawl과 동시에 실행하지 않는다.
