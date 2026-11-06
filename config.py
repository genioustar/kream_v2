# 모델명 전략: bracket_first | slash_first | suffix_single | suffix_grouped | auto
SEARCH_URLS = [
    {
        "site_name": "brickmansion",
        "url": "https://brand.naver.com/brickmansion/best?cp=1",
        "type": "smart_store",
        "model_strategy": "bracket_first",
    },
    {
        "site_name": "set-of",
        "url": "https://brand.naver.com/set-of/category/d03e90228e4f49af9e01eb5eb0907ad2?cp=1",
        "type": "smart_store",
        "model_strategy": "suffix_single",
    },
    {
        "site_name": "loutique",
        "url": "https://smartstore.naver.com/loutique/search?q=%EB%94%94%EC%A0%A4&st=RELEVANCE&dt=IMAGE&page=1&size=80",
        # 검색 URL 직접 이동은 로그인으로 리다이렉트된다. 공개 스토어 검색 UI를 이용한다.
        "navigation": "store_search_ui",
        "type": "smart_store",
        "model_strategy": "suffix_grouped",
    },
    {
        "site_name": "aint",
        "url": "https://smartstore.naver.com/aint/best",
        "type": "smart_store",
        "model_strategy": "suffix_single",
    },
    {
        "site_name": "stellastore",
        "url": "https://smartstore.naver.com/stellastore/best?cp=1",
        "type": "smart_store",
        "model_strategy": "suffix_grouped",
    },
    {
        "site_name": "artemoa",
        "url": "https://smartstore.naver.com/artemoa/best?cp=1",
        "type": "smart_store",
        "model_strategy": "auto",
    },
    {
        "site_name": "burningwear",
        "url": "https://smartstore.naver.com/burningwear/best?cp=1",
        "type": "smart_store",
        "model_strategy": "suffix_grouped",
    },
    {
        "site_name": "reasonershop",
        "url": "https://smartstore.naver.com/reasonershop/category/9e4d920d708943489393d2ca83467562?cp=1",
        "type": "smart_store",
        "model_strategy": "auto",
    },
]
MIN_TRADE_COUNT = 100
MIN_PRICE_DIFF = 5000
# 브라우저 작업은 단일 탭·순차 실행으로 고정한다.
SITE_DELAY_MIN_SEC = 1.0
SITE_DELAY_MAX_SEC = 3.0
OUTPUT_DIR = "output"
# 빈 문자열이면 해당 크롤러는 건너뛴다.
# 예시: https://www.nike.com/kr/w/clearance-shoes-3yaepzy7ok
NIKE_SALE_URL = ""
# 공식 아울렛 전체보기. grid=true가 없으면 캠페인 화면으로 이동한다.
# 예시: https://www.adidas.co.kr/outlet?grid=true
ADIDAS_SALE_URL = ""
# 예시: https://brand.naver.com/adidas/shoppingstory/detail?id=5002700579
SPECIAL_SALE_URL = ""
SPECIAL_MODEL_STRATEGY = "auto"
MUSINSA_SALE_URL = "https://www.musinsa.com/main/outlet/ranking?gf=A&storeCode=outlet&sectionId=233&contentsId=&categoryCode=107002&ageBand=AGE_BAND_ALL"

# 사이트별 수동 할인율 (%) — Kream 외 크롤러에 적용. 0이면 sale_price 미산출.
SITE_DISCOUNT_RATES = {
    "marketplace": 0,
    "adidas": 20,
    "musinsa": 0,
    "nike": 0,
    "special": 0,
}
