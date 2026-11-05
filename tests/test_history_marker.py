"""kream/history_marker.py 회귀 테스트."""
import json
import tempfile
import unittest
from pathlib import Path

from kream.history_marker import (
    find_previous_result_path,
    load_previous_price_map,
    mark_result,
    select_closest_previous_price,
)

TODAY = "20261105"
PREVIOUS_DAY = "20261103"
OLDER_DAY = "20261101"
FUTURE_DAY = "20261106"


def _item(model: str, site: str, price: int) -> dict:
    return {"model_name": model, "marketplace_site": site, "marketplace_price": price}


class HistoryMarkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.today = self.base / TODAY
        self.today.mkdir()

    def _write_result(self, folder: str, items: list[dict]) -> Path:
        d = self.base / folder
        d.mkdir(exist_ok=True)
        path = d / "arbitrage_results.json"
        path.write_text(json.dumps(items), encoding="utf-8")
        return path

    def test_no_previous_folder_marks_all_new(self) -> None:
        self.assertIsNone(find_previous_result_path(self.today))
        prices = load_previous_price_map(self.today)
        self.assertEqual(prices, {})
        marked = mark_result(_item("A", "s", 100), prices)
        self.assertIs(marked["previously_saved"], False)
        self.assertEqual(marked["price_status"], "new")
        self.assertNotIn("previous_marketplace_price", marked)

    def test_today_folder_is_excluded(self) -> None:
        self._write_result(TODAY, [_item("A", "s", 1)])
        self.assertIsNone(find_previous_result_path(self.today))

    def test_future_folder_is_excluded(self) -> None:
        self._write_result(FUTURE_DAY, [_item("A", "s", 1)])
        self.assertIsNone(find_previous_result_path(self.today))

    def test_skips_latest_folder_without_file(self) -> None:
        older = self._write_result(OLDER_DAY, [_item("A", "s", 500)])
        (self.base / PREVIOUS_DAY).mkdir()
        (self.base / "debug").mkdir()
        self.assertEqual(find_previous_result_path(self.today), older)
        self.assertEqual(load_previous_price_map(self.today), {("A", "s"): {500}})

    def test_same_changed_new(self) -> None:
        self._write_result(PREVIOUS_DAY, [_item("A", "s", 100), _item("B", "s", 200)])
        prices = load_previous_price_map(self.today)

        same = mark_result(_item("A", "s", 100), prices)
        self.assertIs(same["previously_saved"], True)
        self.assertEqual(same["price_status"], "same")
        self.assertNotIn("previous_marketplace_price", same)

        changed = mark_result(_item("B", "s", 150), prices)
        self.assertIs(changed["previously_saved"], True)
        self.assertEqual(changed["price_status"], "changed")
        self.assertEqual(changed["previous_marketplace_price"], 200)

        new = mark_result(_item("C", "s", 1), prices)
        self.assertIs(new["previously_saved"], False)
        self.assertEqual(new["price_status"], "new")

    def test_same_model_different_site_is_separate(self) -> None:
        self._write_result(PREVIOUS_DAY, [_item("A", "s1", 100)])
        prices = load_previous_price_map(self.today)
        self.assertEqual(mark_result(_item("A", "s1", 100), prices)["price_status"], "same")
        self.assertEqual(mark_result(_item("A", "s2", 100), prices)["price_status"], "new")

    def test_broken_json_falls_back_to_new(self) -> None:
        d = self.base / PREVIOUS_DAY
        d.mkdir()
        (d / "arbitrage_results.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(load_previous_price_map(self.today), {})

    def test_unexpected_format_falls_back_to_new(self) -> None:
        path = self._write_result(PREVIOUS_DAY, [])
        path.write_text(json.dumps({"a": 1}), encoding="utf-8")
        self.assertEqual(load_previous_price_map(self.today), {})
        path.write_text(json.dumps("text"), encoding="utf-8")
        self.assertEqual(load_previous_price_map(self.today), {})

    def test_duplicate_key_same_prices_are_both_same(self) -> None:
        self._write_result(PREVIOUS_DAY, [_item("A", "s", 44940), _item("A", "s", 37450)])
        prices = load_previous_price_map(self.today)
        self.assertEqual(prices, {("A", "s"): {44940, 37450}})
        for price in (44940, 37450):
            marked = mark_result(_item("A", "s", price), prices)
            self.assertEqual(marked["price_status"], "same")
            self.assertNotIn("previous_marketplace_price", marked)

    def test_duplicate_key_changed_uses_closest_previous(self) -> None:
        prices = {("A", "s"): {44940, 37450}}
        marked = mark_result(_item("A", "s", 40000), prices)
        self.assertEqual(marked["price_status"], "changed")
        self.assertEqual(marked["previous_marketplace_price"], 37450)
        marked = mark_result(_item("A", "s", 50000), prices)
        self.assertEqual(marked["previous_marketplace_price"], 44940)

    def test_closest_previous_tie_picks_smaller(self) -> None:
        self.assertEqual(select_closest_previous_price({100, 200}, 150), 100)
        marked = mark_result(_item("A", "s", 150), {("A", "s"): {200, 100}})
        self.assertEqual(marked["previous_marketplace_price"], 100)

    def test_corrupt_records_are_skipped_individually(self) -> None:
        self._write_result(PREVIOUS_DAY, [
            _item("A", "s", 100),
            {"model_name": "B", "marketplace_site": "s", "marketplace_price": None},
            {"model_name": "C", "marketplace_site": "s", "marketplace_price": "129,000"},
            {"model_name": "D", "marketplace_price": 300},
            "not a dict",
            {"model_name": "E", "marketplace_site": "s", "marketplace_price": "129000"},
        ])
        with self.assertLogs("kream.history_marker", level="WARNING") as logs:
            prices = load_previous_price_map(self.today)
        self.assertEqual(prices, {("A", "s"): {100}, ("E", "s"): {129000}})
        self.assertEqual(len(logs.records), 1)

    def test_unhashable_and_non_finite_records_are_skipped(self) -> None:
        raw = (
            '[{"model_name": "A", "marketplace_site": "s", "marketplace_price": 100},'
            ' {"model_name": ["x"], "marketplace_site": "s", "marketplace_price": 1},'
            ' {"model_name": "B", "marketplace_site": {"k": 1}, "marketplace_price": 1},'
            ' {"model_name": "C", "marketplace_site": "s", "marketplace_price": Infinity},'
            ' {"model_name": "D", "marketplace_site": "s", "marketplace_price": NaN},'
            ' {"model_name": "E", "marketplace_site": "s", "marketplace_price": true},'
            ' {"model_name": "F", "marketplace_site": "s", "marketplace_price": 99.9},'
            ' {"model_name": "G", "marketplace_site": "s", "marketplace_price": 100.0},'
            ' {"model_name": "H", "marketplace_site": "s", "marketplace_price": "129000"}]'
        )
        folder = self.base / PREVIOUS_DAY
        folder.mkdir()
        (folder / "arbitrage_results.json").write_text(raw, encoding="utf-8")
        with self.assertLogs("kream.history_marker", level="WARNING") as logs:
            prices = load_previous_price_map(self.today)
        self.assertEqual(
            prices,
            {("A", "s"): {100}, ("G", "s"): {100}, ("H", "s"): {129000}},
        )
        self.assertEqual(len(logs.records), 1)

    def test_price_diff_string_format_preserved(self) -> None:
        result = {**_item("A", "s", 100), "price_diff": "24,560"}
        marked = mark_result(result, {("A", "s"): {90}})
        self.assertEqual(marked["price_diff"], "24,560")


if __name__ == "__main__":
    unittest.main()
