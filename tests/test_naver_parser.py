import unittest

from naver.parser import clean_url, page_origin


SMARTSTORE_PAGE_URL = "https://smartstore.naver.com/aint/best"
BRAND_PAGE_URL = "https://brand.naver.com/set-of/category/d03e90228e4f49af9e01eb5eb0907ad2?cp=1"
QUERY_PAGE_URL = "https://smartstore.naver.com/loutique/search?q=diesel&st=RELEVANCE&dt=IMAGE&page=1&size=80"
SCHEMELESS_PAGE_URL = "aint/best"
SMARTSTORE_ORIGIN = "https://smartstore.naver.com"
BRAND_ORIGIN = "https://brand.naver.com"
RELATIVE_PRODUCT_PATH = "/aint/products/1"
ABSOLUTE_PRODUCT_URL = "https://smartstore.naver.com/aint/products/1"
PROTOCOL_RELATIVE_URL = "//host/p"
PROTOCOL_RELATIVE_ABSOLUTE_URL = "https://host/p"


class PageOriginTests(unittest.TestCase):
    def test_smartstore_and_brand_urls(self):
        self.assertEqual(page_origin(SMARTSTORE_PAGE_URL), SMARTSTORE_ORIGIN)
        self.assertEqual(page_origin(BRAND_PAGE_URL), BRAND_ORIGIN)

    def test_query_and_path_are_outside_the_origin(self):
        self.assertEqual(page_origin(QUERY_PAGE_URL), SMARTSTORE_ORIGIN)
        self.assertEqual(page_origin(BRAND_PAGE_URL), BRAND_ORIGIN)

    def test_missing_scheme_or_netloc_returns_the_input(self):
        self.assertEqual(page_origin(SCHEMELESS_PAGE_URL), SCHEMELESS_PAGE_URL)


class CleanUrlTests(unittest.TestCase):
    def test_relative_path_uses_the_given_base(self):
        self.assertEqual(
            clean_url(RELATIVE_PRODUCT_PATH, SMARTSTORE_ORIGIN),
            ABSOLUTE_PRODUCT_URL,
        )

    def test_absolute_url_is_unchanged(self):
        self.assertEqual(
            clean_url(ABSOLUTE_PRODUCT_URL, BRAND_ORIGIN),
            ABSOLUTE_PRODUCT_URL,
        )

    def test_protocol_relative_url_gets_https(self):
        self.assertEqual(
            clean_url(PROTOCOL_RELATIVE_URL, BRAND_ORIGIN),
            PROTOCOL_RELATIVE_ABSOLUTE_URL,
        )

    def test_empty_values_are_none(self):
        self.assertIsNone(clean_url("", SMARTSTORE_ORIGIN))
        self.assertIsNone(clean_url(None, SMARTSTORE_ORIGIN))

    def test_base_is_required(self):
        with self.assertRaises(TypeError):
            clean_url(RELATIVE_PRODUCT_PATH)


if __name__ == "__main__":
    unittest.main()
