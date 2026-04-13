PYTHON = venv/bin/python
PYTHONPATH_ENV = PYTHONPATH=$(shell pwd)

.PHONY: chrome adidas crawl kream full

## Chrome CDP 실행 (아디다스 크롤러 필수)
chrome:
	@./chrome-debug.sh

## 아디다스 단독 크롤링
adidas: chrome
	$(PYTHONPATH_ENV) $(PYTHON) adidas/crawler.py

## 전체 크롤링만 (Kream 검색 제외)
crawl: chrome
	$(PYTHONPATH_ENV) $(PYTHON) main.py --mode crawl

## Kream 검색만 (오늘자 *_products.json 사용)
kream:
	$(PYTHONPATH_ENV) $(PYTHON) main.py --mode kream

## 전체 파이프라인
full: chrome
	$(PYTHONPATH_ENV) $(PYTHON) main.py --mode full
