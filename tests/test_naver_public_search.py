import unittest

from naver.public_search import parse_public_search_url


class PublicSearchConfigurationTests(unittest.TestCase):
    def test_original_search_conditions_are_preserved(self):
        options = parse_public_search_url(
            "https://smartstore.naver.com/loutique/search?q=%EB%94%94%EC%A0%A4&st=RELEVANCE&dt=IMAGE&page=1&size=80"
        )
        self.assertEqual(options["home_url"], "https://smartstore.naver.com/loutique")
        self.assertEqual(options["query"], "디젤")
        self.assertEqual(options["size"], 80)

    def test_unsupported_conditions_are_not_silently_changed(self):
        base = "https://smartstore.naver.com/loutique/search?q=TOKEN"
        for url in (
            base + "&page=2", base + "&st=RECENT", base + "&dt=LIST",
            base + "&size=100", base + "&q=second", base + "&size=40&size=80",
            base + "&unknown=1", "https://smartstore.naver.com/loutique/search",
            "https://smartstore.naver.com/loutique/search?q=",
            "https://smartstore.naver.com/loutique/search?q=%20%20",
            "https://smartstore.naver.com.evil.test/loutique/search?q=TOKEN",
            "https://USER:TOKEN@smartstore.naver.com/loutique/search?q=TOKEN",
            "https://smartstore.naver.com:8443/loutique/search?q=TOKEN",
            "https://smartstore.naver.com/loutique/category/all?q=TOKEN",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError) as caught:
                    parse_public_search_url(url)
                self.assertNotIn("TOKEN", str(caught.exception))

    def test_supported_page_sizes_and_brand_store(self):
        for size in (20, 40, 60, 80):
            options = parse_public_search_url(f"https://brand.naver.com/store/search?q=A%20B&size={size}")
            self.assertEqual(options["query"], "A B")
            self.assertEqual(options["size"], size)
