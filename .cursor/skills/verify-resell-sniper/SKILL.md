---
name: verify-resell-sniper
description: resell-sniper 크롤러를 터미널에서 직접 실행해 동작을 증명할 때 사용한다. python chrome.py, python main.py, --mode crawl, --mode kream 변경 후 사용자와 같은 경로로 확인한다.
---

# resell-sniper 검증

저장소 루트에서 가상환경 Python으로 CLI를 실행한다. 브라우저 자동화 도구로 사이트를 대신 열지 않는다. 한 번에 파이프라인은 하나만 띄운다.

## Launch

서버는 없다. 각 명령이 필요할 때 Chrome을 띄운다.

```bash
cd /Users/ny/Documents/programs/resell-sniper
./venv/bin/python chrome.py
./venv/bin/python main.py
./venv/bin/python main.py --mode crawl
./venv/bin/python main.py --mode kream
```

준비 신호는 프로세스 exit code와 로그다. `chrome.py`는 `Chrome 연결 확인 — navigator.webdriver=False` 이후 exit 0이다. `main.py`는 해당 모드의 저장 로그 이후 exit 0이다. 네이버 로그인·보안 화면이 뜨면 열린 Chrome에서 확인한 뒤 같은 터미널에 Enter를 넣는다. 검증 에이전트가 그 화면을 넘길 수 없으면 그 지점과 진단 JSON 경로를 증거로 남기고, 경로를 건너뛰지 않는다.

## Doctor

구동 전에 아래를 확인한다. 실패하면 기능을 돌리지 않는다.

- `./venv/bin/python -c "import playwright"` 가 exit 0.
- `command -v ego-browser` 가 경로를 출력한다.
- 다른 `main.py` 또는 `chrome.py`가 이미 돌고 있으면 중단한다. Task Space 이름은 `resell-sniper` 하나다.

## Drive

기능 지도는 [features/README.md](features/README.md)다. 지도를 읽고 해당 기능 파일의 명령을 그대로 실행한다. 단위 테스트로 이 경로를 대체하지 않는다.

한 번에 하나의 파이프라인만 실행한다. 라이브 사이트 수집은 로그인 대기나 접근 제한으로 멈출 수 있다. 그 경우 로그와 `output/YYYYMMDD/diagnostics/` 의 진단 파일을 증거로 남긴다.

## Evidence

증거 디렉터리는 `.cursor/skills/verify-resell-sniper/artifacts/<feature-id>/` 다. 이 경로는 정리 단계에서 지우지 않는다.

각 실행마다 다음을 저장한다.

- `command.txt` 실행한 명령
- `stdout.log` 와 `stderr.log`
- `exit_code.txt`
- 기능이 파일을 만들면 그 경로를 `outputs.txt` 에 적는다. 상품 JSON 원본은 복사하지 않고 경로와 건수만 적는다.

`chrome.py` 증거의 통과 조건은 exit 0과 로그의 `navigator.webdriver=False` 다.

## Cleanup

수집이 끝나면 코드가 Task Space `resell-sniper` 만 닫는다. ego lite 앱은 종료하지 않는다. `artifacts/` 와 `output/` 의 수집 결과는 삭제하지 않는다.

## Helpers

가상환경 Python은 `./venv/bin/python` 이다. 시스템 `python` 으로 대체하지 않는다.

`ego-browser` 가 있는지 확인한다.

```bash
command -v ego-browser
```
