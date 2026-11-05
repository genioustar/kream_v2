# ego lite 연결 확인

`python chrome.py` 는 사용자의 ego lite 안에 `resell-sniper` Task Space 를 열고, `about:blank` 에서 `navigator.webdriver === true` 가 아닌지 확인한 뒤 그 Space 를 닫는다.

## Sub-features

- `space-open` 은 별도 브라우저 프로세스를 띄우지 않고 Task Space 를 연다.
- `space-webdriver` 는 `navigator.webdriver === true` 가 아님을 로그로 남긴다.
- `space-close` 는 확인 뒤 그 Space 만 닫고 ego lite 앱은 남긴다.

## How to get to it (user POV)

- 저장소 루트 터미널에서 `python chrome.py` 를 실행한다.

## Driving it with the terminal

Preconditions:

- Doctor가 통과했다.
- 다른 `main.py` 가 돌고 있지 않다.
- `ego-browser` 가 PATH 에 있다.

- **연결 확인.** 저장소 루트에서 `./venv/bin/python chrome.py` 를 실행한다. exit 0 이고 stderr 로그에 `navigator.webdriver === true 은 False` 가 있다. Python logging은 stderr로 나간다.
- **Proof.** stdout, stderr, exit code를 `artifacts/chrome-check/` 에 저장한다. 로그에 `False` 가 보여야 한다.

## Gotchas

- 이 명령은 ego lite 를 `--user-data-dir` 로 새로 띄우지 않는다. 앱이 없으면 `ego-browser` 가 연결에 실패한다.
- 확인이 끝나면 Task Space 는 닫힌다. 사용자의 다른 Space 를 종료 대상으로 보지 않는다.
- `navigator.webdriver === true 은 True` 또는 exit 1 은 실패다.
