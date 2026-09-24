"""Persistence tests for GlyphSnap history."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from glyphsnap_storage import HistoryRecord, HistoryStore


class HistoryStoreTests(unittest.TestCase):
    def test_round_trip_delete_and_clear(self) -> None:
        with TemporaryDirectory() as directory:
            store = HistoryStore(Path(directory) / "history.json")
            record = HistoryRecord.create(
                source_type="image",
                source_name="sample.png",
                source_path="C:/sample.png",
                languages=["ara", "eng"],
                processing_seconds=1.25,
                text="مرحبا GlyphSnap",
            )
            store.add(record)
            loaded = store.load()
            self.assertEqual([item.id for item in loaded], [record.id])
            self.assertEqual(loaded[0].text, record.text)
            self.assertEqual(loaded[0].word_count, 2)
            store.delete(record.id)
            self.assertEqual(store.load(), [])
            store.add(record)
            store.clear()
            self.assertEqual(store.load(), [])

    def test_invalid_record_does_not_hide_valid_records(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            path.write_text(
                '[{"id": "valid", "created_at": "2026-09-24T00:00:00+00:00", '
                '"text": "hello"}, {"created_at": "missing id"}]',
                encoding="utf-8",
            )
            loaded = HistoryStore(path).load()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].id, "valid")


if __name__ == "__main__":
    unittest.main()
