import io
import json
import socket
import unittest
from unittest.mock import AsyncMock, call, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import config
from common.pacing import CrawlBlockedError
from musinsa import crawler

BRAND_SALE_URL = "https://www.musinsa.com/brand/adidas/products"
RANKING_SALE_URL = "https://www.musinsa.com/main/outlet/ranking?categoryCode=107002"
FIRST_PAGE_NUMBER = 1
FAILING_PAGE_NUMBER = 2
TOTAL_PAGES = 3
FULL_COLLECTION_DELAY_COUNT = TOTAL_PAGES - 1
EARLY_STOP_DELAY_COUNT = FAILING_PAGE_NUMBER - 1
BASE_PRICE = 50000
HTTP_TOO_MANY_REQUESTS = 429
HTTP_FORBIDDEN = 403
HTTP_SERVER_ERROR = 500
BRAND_PAGE_CAP = 4
SAME_NEXT_PAGE_URL = "https://api.musinsa.com/same"
CAMPAIGN_SALE_URL = (
    "https://www.musinsa.com/campaign/mujinjangsale/mujinjangIndex?store=musinsa&tab=recommend"
)
UNSUPPORTED_SALE_URL = "https://www.musinsa.com/app/goods/1"
EXPECTED_SORT_CODE = "DISCOUNT_RATE"
EXPECTED_CATEGORY = "103"
MIN_DISCOUNT_RATE = 30
HIGH_DISCOUNT_RATE = 50
MID_DISCOUNT_RATE = 40
LOW_DISCOUNT_RATE = 10
JUST_BELOW_MIN_RATE = MIN_DISCOUNT_RATE - 1
BRAND_MODEL_BASE = {"adidas": 100, "puma": 200, "vans": 300}
SECOND_PAGE_START = 2
THIRD_PAGE_START = 4


def http_error(status):
    return HTTPError("https://api.musinsa.com/x", status, "error", {}, io.BytesIO())


def model_code(page):
    return f"AB{page}123"


def brand_page(page):
    return {"data": {
        "list": [{
            "goodsName": f"Adidas Shoe {model_code(page)}",
            "goodsLinkUrl": f"https://www.musinsa.com/products/{page}",
            "finalPrice": BASE_PRICE + page,
            "isSoldOut": False,
        }],
        "pagination": {
            "page": page,
            "hasNext": page < TOTAL_PAGES,
            "nextPageUrl": f"https://api.musinsa.com/next{page + 1}",
            "totalCount": TOTAL_PAGES,
        },
    }}


def brand_page_with_pagination(page, has_next, next_page_url):
    payload = brand_page(page)
    pagination = payload["data"]["pagination"]
    pagination["hasNext"] = has_next
    pagination["nextPageUrl"] = next_page_url
    return payload


def brand_pages_ending(has_next, next_page_url):
    """1페이지는 다음 주소가 있고, FAILING_PAGE_NUMBER 페이지에서 페이지네이션을 지정한다."""
    def make_page(page):
        if page < FAILING_PAGE_NUMBER:
            return brand_page(page)
        return brand_page_with_pagination(page, has_next, next_page_url)

    return make_page


def ranking_page(page, has_next=True):
    items = [{
        "type": "PRODUCT_COLUMN",
        "info": {
            "productName": f"Adidas Shoe {model_code(page)}",
            "finalPrice": BASE_PRICE + page,
            "isSoldOut": False,
        },
        "onClick": {"url": f"https://www.musinsa.com/products/{page}"},
    }]
    link = {"next": f"https://client.musinsa.com/next{page + 1}"} if has_next else {}
    return {"data": {"modules": [{"type": "MULTICOLUMN", "items": items}]}, "link": link}


def empty_ranking_page():
    return {"data": {"modules": []}, "link": {}}


def fake_fetch(make_page, failure=None):
    """FAILING_PAGE_NUMBER번째 요청에서 failure를 던지고, 그 외에는 make_page(n)를 돌려준다."""
    calls = {"count": 0}

    async def fetch(url, referer):
        calls["count"] += 1
        if failure is not None and calls["count"] == FAILING_PAGE_NUMBER:
            raise failure
        return make_page(calls["count"])

    return fetch


class FakeResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self):
        return self.body


def fake_urlopen(make_page, status, failing_call=FAILING_PAGE_NUMBER):
    """실제 _fetch_json을 거치도록 urlopen 단계에서 failing_call번째 요청에 HTTP 오류를 낸다."""
    calls = {"count": 0}

    def urlopen(request, timeout):
        calls["count"] += 1
        if calls["count"] == failing_call:
            raise http_error(status)
        return FakeResponse(make_page(calls["count"]))

    return urlopen


class MusinsaCollectionTestCase(unittest.IsolatedAsyncioTestCase):
    async def crawl_with_http_error(self, sale_url, make_page, status, failing_call=FAILING_PAGE_NUMBER):
        with patch.object(config, "MUSINSA_SALE_URL", sale_url), \
                patch.object(crawler, "urlopen", fake_urlopen(make_page, status, failing_call)), \
                patch.object(crawler, "REQUEST_DELAY_SEC", 0):
            return await crawler.crawl_musinsa()

    async def crawl(self, sale_url, fetch):
        with patch.object(config, "MUSINSA_SALE_URL", sale_url), \
                patch.object(crawler, "_fetch_json_async", fetch), \
                patch.object(crawler, "REQUEST_DELAY_SEC", 0):
            return await crawler.crawl_musinsa()


class BrandCollectionTests(MusinsaCollectionTestCase):
    async def crawl_recording_delay(self, sale_url, fetch):
        """간격 상수는 0으로 바꾸지 않고, 페이지 사이 sleep 호출만 기록한다.

        크롤이 예외를 내면 그 예외를 그대로 올린다. 그때까지 기록은 self.recorded_delay 에 있다.
        """
        sleep = AsyncMock()
        self.recorded_delay = sleep
        with patch.object(config, "MUSINSA_SALE_URL", sale_url), \
                patch.object(crawler, "_fetch_json_async", fetch), \
                patch.object(crawler.asyncio, "sleep", sleep):
            products = await crawler.crawl_musinsa()
        return products, sleep

    async def test_all_pages_are_collected(self):
        products = await self.crawl(BRAND_SALE_URL, fake_fetch(brand_page))
        self.assertEqual(
            [p.model_name for p in products],
            [model_code(page) for page in range(1, TOTAL_PAGES + 1)],
        )

    async def test_429_midway_is_pipeline_block(self):
        with self.assertRaises(CrawlBlockedError):
            await self.crawl_with_http_error(BRAND_SALE_URL, brand_page, HTTP_TOO_MANY_REQUESTS)

    async def test_403_midway_is_pipeline_block(self):
        with self.assertRaises(CrawlBlockedError):
            await self.crawl_with_http_error(BRAND_SALE_URL, brand_page, HTTP_FORBIDDEN)

    async def test_500_midway_propagates_as_plain_http_error(self):
        with self.assertRaises(HTTPError) as caught:
            await self.crawl(BRAND_SALE_URL, fake_fetch(brand_page, http_error(HTTP_SERVER_ERROR)))
        self.assertNotIsInstance(caught.exception, CrawlBlockedError)
        self.assertEqual(caught.exception.code, HTTP_SERVER_ERROR)

    async def test_timeout_midway_propagates(self):
        with self.assertRaises(socket.timeout):
            await self.crawl(BRAND_SALE_URL, fake_fetch(brand_page, socket.timeout("timed out")))

    async def test_500_on_first_page_propagates(self):
        with self.assertRaises(HTTPError) as caught:
            await self.crawl_with_http_error(
                BRAND_SALE_URL,
                brand_page,
                HTTP_SERVER_ERROR,
                FIRST_PAGE_NUMBER,
            )
        self.assertNotIsInstance(caught.exception, CrawlBlockedError)
        self.assertEqual(caught.exception.code, HTTP_SERVER_ERROR)

    async def test_429_on_first_page_is_pipeline_block(self):
        with self.assertRaises(CrawlBlockedError):
            await self.crawl_with_http_error(
                BRAND_SALE_URL,
                brand_page,
                HTTP_TOO_MANY_REQUESTS,
                FIRST_PAGE_NUMBER,
            )

    async def test_has_next_without_next_url_raises(self):
        with self.assertRaises(RuntimeError) as caught:
            await self.crawl(BRAND_SALE_URL, fake_fetch(brand_pages_ending(True, None)))
        self.assertNotIsInstance(caught.exception, CrawlBlockedError)
        self.assertIn(str(FAILING_PAGE_NUMBER), str(caught.exception))

    async def test_has_next_with_blank_next_url_raises(self):
        with self.assertRaises(RuntimeError) as caught:
            await self.crawl(BRAND_SALE_URL, fake_fetch(brand_pages_ending(True, "")))
        self.assertNotIsInstance(caught.exception, CrawlBlockedError)
        self.assertIn(str(FAILING_PAGE_NUMBER), str(caught.exception))

    async def test_no_has_next_returns_collected_products(self):
        products = await self.crawl(
            BRAND_SALE_URL,
            fake_fetch(brand_pages_ending(False, None)),
        )
        self.assertEqual(
            [product.model_name for product in products],
            [model_code(page) for page in range(1, FAILING_PAGE_NUMBER + 1)],
        )

    async def test_full_collection_sleeps_between_pages(self):
        products, sleep = await self.crawl_recording_delay(BRAND_SALE_URL, fake_fetch(brand_page))
        self.assertEqual(
            [product.model_name for product in products],
            [model_code(page) for page in range(1, TOTAL_PAGES + 1)],
        )
        self.assertEqual(sleep.await_count, FULL_COLLECTION_DELAY_COUNT)
        self.assertEqual(
            sleep.await_args_list,
            [call(crawler.REQUEST_DELAY_SEC)] * FULL_COLLECTION_DELAY_COUNT,
        )

    async def test_last_page_does_not_sleep(self):
        products, sleep = await self.crawl_recording_delay(
            BRAND_SALE_URL,
            fake_fetch(brand_pages_ending(False, None)),
        )
        self.assertEqual(
            [product.model_name for product in products],
            [model_code(page) for page in range(1, FAILING_PAGE_NUMBER + 1)],
        )
        self.assertEqual(sleep.await_count, EARLY_STOP_DELAY_COUNT)
        self.assertEqual(
            sleep.await_args_list,
            [call(crawler.REQUEST_DELAY_SEC)] * EARLY_STOP_DELAY_COUNT,
        )

    async def test_repeated_next_page_url_raises(self):
        calls = {"count": 0}

        async def fetch(url, referer):
            calls["count"] += 1
            return brand_page_with_pagination(calls["count"], True, SAME_NEXT_PAGE_URL)

        with self.assertRaises(RuntimeError) as caught:
            await self.crawl_recording_delay(BRAND_SALE_URL, fetch)
        self.assertNotIsInstance(caught.exception, CrawlBlockedError)
        self.assertIn("다음 페이지 주소가 반복됩니다", str(caught.exception))
        self.assertIn(f"page={FAILING_PAGE_NUMBER}", str(caught.exception))
        self.assertEqual(calls["count"], FAILING_PAGE_NUMBER)
        repeated_url_delay_count = FAILING_PAGE_NUMBER - 1
        self.assertEqual(self.recorded_delay.await_count, repeated_url_delay_count)
        self.assertEqual(
            self.recorded_delay.await_args_list,
            [call(crawler.REQUEST_DELAY_SEC)] * repeated_url_delay_count,
        )

    async def test_repeated_first_request_url_raises(self):
        calls = {"count": 0, "first_url": None}

        async def fetch(url, referer):
            calls["count"] += 1
            if calls["first_url"] is None:
                calls["first_url"] = url
            return brand_page_with_pagination(calls["count"], True, calls["first_url"])

        with self.assertRaises(RuntimeError) as caught:
            await self.crawl(BRAND_SALE_URL, fetch)
        self.assertNotIsInstance(caught.exception, CrawlBlockedError)
        self.assertIn("다음 페이지 주소가 반복됩니다", str(caught.exception))
        self.assertIn(f"page={FIRST_PAGE_NUMBER}", str(caught.exception))
        self.assertEqual(calls["count"], FIRST_PAGE_NUMBER)

    async def test_endless_distinct_urls_raise_at_brand_page_cap(self):
        calls = {"count": 0}

        async def fetch(url, referer):
            calls["count"] += 1
            return brand_page_with_pagination(
                calls["count"],
                True,
                f"https://api.musinsa.com/next{calls['count'] + 1}",
            )

        with patch.object(crawler, "MAX_BRAND_PAGES", BRAND_PAGE_CAP):
            with self.assertRaises(RuntimeError) as caught:
                await self.crawl_recording_delay(BRAND_SALE_URL, fetch)
        self.assertNotIsInstance(caught.exception, CrawlBlockedError)
        self.assertIn(f"최대 페이지({BRAND_PAGE_CAP})", str(caught.exception))
        self.assertEqual(calls["count"], BRAND_PAGE_CAP)
        cap_delay_count = BRAND_PAGE_CAP - 1
        self.assertEqual(self.recorded_delay.await_count, cap_delay_count)
        self.assertEqual(
            self.recorded_delay.await_args_list,
            [call(crawler.REQUEST_DELAY_SEC)] * cap_delay_count,
        )

    async def test_has_next_false_at_brand_page_cap_returns_products(self):
        calls = {"count": 0}

        async def fetch(url, referer):
            calls["count"] += 1
            has_next = calls["count"] < BRAND_PAGE_CAP
            next_page_url = f"https://api.musinsa.com/next{calls['count'] + 1}" if has_next else None
            return brand_page_with_pagination(calls["count"], has_next, next_page_url)

        with patch.object(crawler, "MAX_BRAND_PAGES", BRAND_PAGE_CAP):
            products = await self.crawl(BRAND_SALE_URL, fetch)
        self.assertEqual(
            [product.model_name for product in products],
            [model_code(page) for page in range(1, BRAND_PAGE_CAP + 1)],
        )
        self.assertEqual(calls["count"], BRAND_PAGE_CAP)


class RankingCollectionTests(MusinsaCollectionTestCase):
    async def test_ranking_pages_are_collected(self):
        def make_page(page):
            return ranking_page(page, has_next=page < TOTAL_PAGES)

        products = await self.crawl(RANKING_SALE_URL, fake_fetch(make_page))
        self.assertEqual(
            [p.model_name for p in products],
            [model_code(page) for page in range(1, TOTAL_PAGES + 1)],
        )

    async def test_429_midway_is_pipeline_block(self):
        with self.assertRaises(CrawlBlockedError):
            await self.crawl_with_http_error(RANKING_SALE_URL, ranking_page, HTTP_TOO_MANY_REQUESTS)

    async def test_403_on_second_page_is_pipeline_block(self):
        with self.assertRaises(CrawlBlockedError):
            await self.crawl_with_http_error(RANKING_SALE_URL, ranking_page, HTTP_FORBIDDEN)

    async def test_500_midway_propagates_as_plain_http_error(self):
        with self.assertRaises(HTTPError) as caught:
            await self.crawl(RANKING_SALE_URL, fake_fetch(ranking_page, http_error(HTTP_SERVER_ERROR)))
        self.assertNotIsInstance(caught.exception, CrawlBlockedError)

    async def test_empty_page_ends_normally_with_collected_products(self):
        def make_page(page):
            return ranking_page(page) if page < FAILING_PAGE_NUMBER else empty_ranking_page()

        products = await self.crawl(RANKING_SALE_URL, fake_fetch(make_page))
        self.assertEqual([p.model_name for p in products], [model_code(1)])


def sale_item(brand, position, **fields):
    """브랜드별로 모델코드가 겹치지 않는 PLP 항목. 할인율 등은 fields 로 넣는다."""
    model_index = BRAND_MODEL_BASE[brand] + position
    return {
        "goodsName": f"Adidas Shoe {model_code(model_index)}",
        "goodsLinkUrl": f"https://www.musinsa.com/products/{model_index}",
        "finalPrice": BASE_PRICE + model_index,
        "isSoldOut": False,
        **fields,
    }


def rated_items(brand, rates, start=0):
    return [
        sale_item(brand, start + offset, saleRate=rate)
        for offset, rate in enumerate(rates)
    ]


def model_codes(brand, positions):
    return [model_code(BRAND_MODEL_BASE[brand] + position) for position in positions]


class FakeBrandApi:
    """브랜드별 PLP 페이지 목록을 돌려주고 요청 URL 을 기록한다."""

    def __init__(self, pages_by_brand):
        self.pages_by_brand = pages_by_brand
        self.requests = []

    def page_for(self, url):
        self.requests.append(url)
        query = parse_qs(urlparse(url).query)
        brand = query["brand"][0]
        page = int(query["page"][0])
        pages = self.pages_by_brand[brand]
        has_next = page < len(pages)
        next_page_url = (
            f"https://api.musinsa.com/next?brand={brand}&page={page + 1}" if has_next else None
        )
        return {"data": {
            "list": pages[page - 1],
            "pagination": {
                "page": page,
                "hasNext": has_next,
                "nextPageUrl": next_page_url,
                "totalCount": sum(len(items) for items in pages),
            },
        }}

    async def __call__(self, url, referer):
        return self.page_for(url)

    def requested_queries(self):
        return [parse_qs(urlparse(url).query) for url in self.requests]

    def requested_brands(self):
        return [query["brand"][0] for query in self.requested_queries()]


def urlopen_serving(api, status, failing_call):
    """실제 _fetch_json 을 거치도록 urlopen 단계에서 failing_call번째 요청에 HTTP 오류를 낸다."""
    calls = {"count": 0}

    def urlopen(request, timeout):
        calls["count"] += 1
        if calls["count"] == failing_call:
            raise http_error(status)
        return FakeResponse(api.page_for(request.full_url))

    return urlopen


class KreamBrandDiscountTests(MusinsaCollectionTestCase):
    BRANDS = ("adidas", "puma")

    def patch_discount_config(self, brands):
        return patch.multiple(
            config,
            MUSINSA_KREAM_BRANDS=brands,
            MUSINSA_MIN_DISCOUNT_RATE=MIN_DISCOUNT_RATE,
        )

    async def crawl_campaign(self, api, brands=BRANDS):
        with self.patch_discount_config(brands):
            return await self.crawl(CAMPAIGN_SALE_URL, api)

    async def crawl_campaign_recording_delay(self, api, brands=BRANDS):
        """간격 상수는 0으로 바꾸지 않고, 브랜드·페이지 사이 sleep 호출만 기록한다."""
        sleep = AsyncMock()
        with self.patch_discount_config(brands), \
                patch.object(config, "MUSINSA_SALE_URL", CAMPAIGN_SALE_URL), \
                patch.object(crawler, "_fetch_json_async", api), \
                patch.object(crawler.asyncio, "sleep", sleep):
            products = await crawler.crawl_musinsa()
        return products, sleep

    async def test_each_brand_is_requested_sorted_by_discount_rate(self):
        api = FakeBrandApi({
            "adidas": [rated_items("adidas", [HIGH_DISCOUNT_RATE])],
            "puma": [rated_items("puma", [HIGH_DISCOUNT_RATE])],
        })
        await self.crawl_campaign(api)

        self.assertEqual(api.requested_brands(), ["adidas", "puma"])
        for query in api.requested_queries():
            with self.subTest(brand=query["brand"][0]):
                self.assertEqual(query["sortCode"], [EXPECTED_SORT_CODE])
                self.assertEqual(query["category"], [EXPECTED_CATEGORY])
                self.assertNotIn("tab", query)
                self.assertNotIn("store", query)

    async def test_minimum_rate_is_kept_and_lower_rate_stops_without_next_page(self):
        api = FakeBrandApi({"adidas": [
            rated_items("adidas", [HIGH_DISCOUNT_RATE, MIN_DISCOUNT_RATE, JUST_BELOW_MIN_RATE]),
            rated_items("adidas", [HIGH_DISCOUNT_RATE], start=THIRD_PAGE_START),
        ]})
        products = await self.crawl_campaign(api, brands=("adidas",))

        self.assertEqual([p.model_name for p in products], model_codes("adidas", [0, 1]))
        self.assertEqual(len(api.requests), 1)

    async def test_stop_on_later_page_keeps_earlier_pages_and_skips_the_rest(self):
        api = FakeBrandApi({"adidas": [
            rated_items("adidas", [HIGH_DISCOUNT_RATE, MID_DISCOUNT_RATE]),
            rated_items("adidas", [MIN_DISCOUNT_RATE, JUST_BELOW_MIN_RATE], start=SECOND_PAGE_START),
            rated_items("adidas", [HIGH_DISCOUNT_RATE], start=THIRD_PAGE_START),
        ]})
        products = await self.crawl_campaign(api, brands=("adidas",))

        self.assertEqual([p.model_name for p in products], model_codes("adidas", [0, 1, 2]))
        self.assertEqual(len(api.requests), SECOND_PAGE_START)

    async def test_items_after_the_stop_item_are_discarded(self):
        api = FakeBrandApi({"adidas": [
            rated_items("adidas", [HIGH_DISCOUNT_RATE, JUST_BELOW_MIN_RATE, HIGH_DISCOUNT_RATE]),
        ]})
        products = await self.crawl_campaign(api, brands=("adidas",))

        self.assertEqual([p.model_name for p in products], model_codes("adidas", [0]))

    async def test_sold_out_item_below_minimum_still_stops_collection(self):
        api = FakeBrandApi({"adidas": [
            [
                sale_item("adidas", 0, saleRate=HIGH_DISCOUNT_RATE),
                sale_item("adidas", 1, saleRate=LOW_DISCOUNT_RATE, isSoldOut=True),
            ],
            rated_items("adidas", [HIGH_DISCOUNT_RATE], start=SECOND_PAGE_START),
        ]})
        products = await self.crawl_campaign(api, brands=("adidas",))

        self.assertEqual([p.model_name for p in products], model_codes("adidas", [0]))
        self.assertEqual(len(api.requests), 1)

    async def test_missing_or_non_integer_sale_rate_counts_as_zero_discount(self):
        unreadable_rates = {
            "missing": {},
            "none": {"saleRate": None},
            "string": {"saleRate": str(HIGH_DISCOUNT_RATE)},
        }
        for label, fields in unreadable_rates.items():
            with self.subTest(sale_rate=label):
                api = FakeBrandApi({"adidas": [[
                    sale_item("adidas", 0, saleRate=HIGH_DISCOUNT_RATE),
                    sale_item("adidas", 1, **fields),
                    sale_item("adidas", 2, saleRate=HIGH_DISCOUNT_RATE),
                ]]})
                products = await self.crawl_campaign(api, brands=("adidas",))

                self.assertEqual([p.model_name for p in products], model_codes("adidas", [0]))

    async def test_brands_are_combined_in_config_order_and_a_stopped_brand_does_not_end_the_crawl(self):
        brands = ("vans", "puma", "adidas")
        api = FakeBrandApi({
            "vans": [rated_items("vans", [HIGH_DISCOUNT_RATE, MID_DISCOUNT_RATE])],
            "puma": [rated_items("puma", [HIGH_DISCOUNT_RATE, JUST_BELOW_MIN_RATE])],
            "adidas": [rated_items("adidas", [MIN_DISCOUNT_RATE])],
        })
        products = await self.crawl_campaign(api, brands=brands)

        self.assertEqual(
            [p.model_name for p in products],
            model_codes("vans", [0, 1]) + model_codes("puma", [0]) + model_codes("adidas", [0]),
        )
        self.assertEqual(api.requested_brands(), list(brands))

    async def test_sleeps_between_brands_but_not_after_the_last(self):
        brands = ("adidas", "puma", "vans")
        api = FakeBrandApi({
            brand: [rated_items(brand, [HIGH_DISCOUNT_RATE])] for brand in brands
        })
        _, sleep = await self.crawl_campaign_recording_delay(api, brands=brands)

        between_brand_delay_count = len(brands) - 1
        self.assertEqual(
            sleep.await_args_list,
            [call(crawler.REQUEST_DELAY_SEC)] * between_brand_delay_count,
        )

    async def test_single_brand_stopped_early_does_not_sleep(self):
        api = FakeBrandApi({"adidas": [
            rated_items("adidas", [HIGH_DISCOUNT_RATE, JUST_BELOW_MIN_RATE]),
            rated_items("adidas", [HIGH_DISCOUNT_RATE], start=SECOND_PAGE_START),
        ]})
        _, sleep = await self.crawl_campaign_recording_delay(api, brands=("adidas",))

        self.assertEqual(sleep.await_count, 0)

    async def test_block_on_later_brand_raises_without_partial_result(self):
        for status in (HTTP_FORBIDDEN, HTTP_TOO_MANY_REQUESTS):
            with self.subTest(status=status):
                api = FakeBrandApi({
                    "adidas": [rated_items("adidas", [HIGH_DISCOUNT_RATE])],
                    "puma": [rated_items("puma", [HIGH_DISCOUNT_RATE])],
                })
                second_brand_request = 2
                with self.patch_discount_config(self.BRANDS), \
                        patch.object(config, "MUSINSA_SALE_URL", CAMPAIGN_SALE_URL), \
                        patch.object(crawler, "urlopen", urlopen_serving(api, status, second_brand_request)), \
                        patch.object(crawler, "REQUEST_DELAY_SEC", 0):
                    with self.assertRaises(CrawlBlockedError):
                        await crawler.crawl_musinsa()

    async def test_empty_brand_list_returns_empty_without_requests(self):
        api = FakeBrandApi({})
        with self.assertLogs(crawler.logger, level="WARNING"):
            products = await self.crawl_campaign(api, brands=())

        self.assertEqual(products, [])
        self.assertEqual(api.requests, [])

    async def test_stop_at_first_item_logs_warning_with_brand_and_sale_rate(self):
        api = FakeBrandApi({"adidas": [rated_items("adidas", [LOW_DISCOUNT_RATE, HIGH_DISCOUNT_RATE])]})
        with self.assertLogs(crawler.logger, level="WARNING") as logs:
            products = await self.crawl_campaign(api, brands=("adidas",))

        self.assertEqual(products, [])
        self.assertEqual(len(logs.output), 1)
        self.assertIn("brand=adidas", logs.output[0])
        self.assertIn(f"saleRate={LOW_DISCOUNT_RATE}%", logs.output[0])

    async def test_stop_after_collecting_items_logs_no_warning(self):
        api = FakeBrandApi({"adidas": [
            rated_items("adidas", [HIGH_DISCOUNT_RATE, JUST_BELOW_MIN_RATE]),
        ]})
        with self.assertNoLogs(crawler.logger, level="WARNING"):
            products = await self.crawl_campaign(api, brands=("adidas",))

        self.assertEqual([p.model_name for p in products], model_codes("adidas", [0]))

    async def test_brand_url_mode_keeps_all_discount_rates_and_default_sort(self):
        api = FakeBrandApi({"adidas": [rated_items("adidas", [LOW_DISCOUNT_RATE, LOW_DISCOUNT_RATE])]})
        products = await self.crawl(BRAND_SALE_URL, api)

        self.assertEqual([p.model_name for p in products], model_codes("adidas", [0, 1]))
        self.assertNotIn("sortCode", api.requested_queries()[0])
        self.assertNotIn("category", api.requested_queries()[0])

    async def test_unsupported_url_warning_names_the_campaign_format(self):
        api = FakeBrandApi({})
        with self.assertLogs(crawler.logger, level="WARNING") as logs:
            products = await self.crawl(UNSUPPORTED_SALE_URL, api)

        self.assertEqual(products, [])
        self.assertIn("/campaign/", logs.output[0])


class FetchJsonTests(unittest.TestCase):
    def fetch_with_error(self, status):
        with patch.object(crawler, "urlopen", side_effect=http_error(status)):
            crawler._fetch_json("https://api.musinsa.com/x", "https://www.musinsa.com/")

    def test_429_becomes_pipeline_block(self):
        with self.assertRaises(CrawlBlockedError):
            self.fetch_with_error(HTTP_TOO_MANY_REQUESTS)

    def test_403_becomes_pipeline_block(self):
        with self.assertRaises(CrawlBlockedError):
            self.fetch_with_error(HTTP_FORBIDDEN)

    def test_500_is_raised_unchanged(self):
        with self.assertRaises(HTTPError) as caught:
            self.fetch_with_error(HTTP_SERVER_ERROR)
        self.assertNotIsInstance(caught.exception, CrawlBlockedError)
        self.assertEqual(caught.exception.code, HTTP_SERVER_ERROR)


if __name__ == "__main__":
    unittest.main()
