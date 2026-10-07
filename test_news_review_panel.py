import contextlib
import unittest
from unittest import mock

import news_review_panel as panel

RATING = {"rating": "positive", "confidence": 0.6, "causality": "direct",
          "reason": "Order win", "sources": ["https://example.com/a"]}


class FakeSt:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name == "columns":
                return [FakeSt() for _ in range(args[0])]
            if name in ("container", "expander"):
                return contextlib.nullcontext()
            if name == "selectbox":
                return args[1][0] if args[1] else None
            return None
        return record

    def text(self):
        return " ".join(str(a) for _, args, _ in self.calls for a in args)


def fake_load(codex, claude):
    def load(name):
        return codex if "codex" in name else claude
    return load


class PanelTests(unittest.TestCase):
    def run_panel(self, codex, claude):
        st = FakeSt()
        with mock.patch.object(panel, "load", fake_load(codex, claude)), \
             mock.patch.object(panel.config, "STOCKS", ["PSO", "OGDC"]):
            panel.show(st)
        return st

    def test_stale_codex_no_longer_hides_fresh_claude(self):
        st = self.run_panel(({}, {"status": "stale"}),
                            ({"PSO": RATING}, {"status": "ok", "as_of": "2026-10-02T04:38Z",
                                               "age_hours": 0.3}))
        table = next(args[0] for name, args, _ in st.calls if name == "dataframe")
        pso = next(r for r in table if r["Stock"] == "PSO")
        self.assertEqual(pso["Claude"], "Positive")
        self.assertEqual(pso["Codex"], "Review stale")
        self.assertIn("Codex review: stale", st.text())

    def test_stale_ratings_are_never_shown_as_current(self):
        st = self.run_panel(({}, {"status": "stale"}), ({}, {"status": "stale"}))
        self.assertFalse(any(name == "dataframe" for name, _, _ in st.calls))
        self.assertIn("No fresh news review", st.text())

    def test_both_fresh_shows_both(self):
        st = self.run_panel(({"OGDC": RATING}, {"status": "ok", "as_of": "x", "age_hours": 1.0}),
                            ({"PSO": RATING}, {"status": "ok", "as_of": "y", "age_hours": 0.5}))
        table = next(args[0] for name, args, _ in st.calls if name == "dataframe")
        rows = {r["Stock"]: r for r in table}
        self.assertEqual(rows["OGDC"]["Codex"], "Positive")
        self.assertEqual(rows["PSO"]["Claude"], "Positive")
        self.assertEqual(rows["PSO"]["Codex"], "No reviewed news")


if __name__ == "__main__":
    unittest.main()
