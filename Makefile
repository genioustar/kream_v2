ifeq ($(OS),Windows_NT)
    PYTHON     = .venv/Scripts/python
    CHROME_BIN = C:/Program Files/Google/Chrome/Application/chrome.exe
    CHROME_DIR = C:/Temp/chrome-cdp
else
    PYTHON     = .venv/bin/python
    CHROME_BIN = /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
    CHROME_DIR = /tmp/chrome-cdp
endif

.PHONY: chrome adidas nike crawl kream full

## Chrome CDP 실행 (adidas·nike 크롤러 사전 실행 필요)
chrome:
	@if curl -s http://127.0.0.1:9222/json/version > /dev/null 2>&1; then \
		echo "Chrome CDP already running (port 9222)"; \
	else \
		"$(CHROME_BIN)" --remote-debugging-port=9222 \
			--user-data-dir="$(CHROME_DIR)" --no-first-run & \
		sleep 3; \
		curl -s http://127.0.0.1:9222/json/version > /dev/null 2>&1 \
			&& echo "Chrome CDP ready (port 9222)" \
			|| echo "Chrome CDP not ready yet — retry in a moment"; \
	fi

## 아디다스 단독 크롤링
adidas:
	PYTHONPATH=$(CURDIR) $(PYTHON) adidas/crawler.py

## 나이키 단독 크롤링
nike:
	PYTHONPATH=$(CURDIR) $(PYTHON) nike/crawler.py

## 전체 크롤링만 (Kream 검색 제외)
crawl:
	PYTHONPATH=$(CURDIR) $(PYTHON) main.py --mode crawl

## Kream 검색만 (오늘자 *_products.json 사용)
kream:
	PYTHONPATH=$(CURDIR) $(PYTHON) main.py --mode kream

## 전체 파이프라인 (adidas·nike는 make chrome 선행 필요)
full:
	PYTHONPATH=$(CURDIR) $(PYTHON) main.py --mode full
