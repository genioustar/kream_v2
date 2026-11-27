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
MUSINSA_SALE_URL = "https://www.musinsa.com/campaign/mujinjangsale/recommended"
# MUSINSA_SALE_URL 이 /campaign/ 으로 시작하면 이 브랜드만 수집한다. 과거 Kream 과 모델이 일치한 브랜드이며 사용자가 추가·삭제한다.
MUSINSA_KREAM_BRANDS: tuple[str, ...] = ("adidas", "puma", "vans", "reebok", "crocs", "converse", "camper", "underarmour", "nike", "ugg")
# 브랜드마다 할인율 내림차순으로 받다가 이 값(%) 미만이 나오면 멈춘다. 과거 차익 상품이 할인 30% 이상이었다는 추정이며 검증한 값이 아니다.
MUSINSA_MIN_DISCOUNT_RATE = 30
# 캠페인 모드에서 PLP API query category 로 넘기는 무신사 신발 카테고리 코드. 과거 무신사 차익 7건이 모두 신발이었고,
# 의류·양말까지 받으면 8개 브랜드에서 3653개를 수집해 예전 아울렛 랭킹 수집(약 600개)의 6배가 된다(2026-11-24 실측, 신발만 2282개·86초).
MUSINSA_KREAM_CATEGORY = "103"
# SSF SHOP ADIDAS KOREA 브랜드관 전체 상품. 의류·잡화·신발을 URL 그대로 수집한다.
SSFSHOP_SALE_URL = "https://www.ssfshop.com/ADIDAS-KOREA/main?dspCtgryNo=SFM&brandShopNo=BDMA29AKU&brndShopId=RTADA&currentPage=1&sortColumn=BEST_DC_SEQ&serviceType=DSP&ctgrySectCd=GNRL_CTGRY&fitPsbYn=N&subBrndCtgryGodYn=Y&cnrSn=23416&cnrSetSn=367764"

# 사이트별 수동 할인율 (%) — Kream 외 크롤러에 적용. 0이면 sale_price 미산출.
SITE_DISCOUNT_RATES = {
    "marketplace": 0,
    "adidas": 20,
    "musinsa": 0,
    "nike": 0,
    "special": 0,
    "ssfshop": 0,
}
