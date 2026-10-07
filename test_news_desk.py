import unittest
from unittest import mock

import news_desk
import opportunity_cards

RAW = {"fetched_at": "2026-10-02T08:36:00+00:00", "items": [
    {"title": "Ronaldo-less Portugal win - Dawn", "url": "https://www.dawn.com/s", "published": "2026-10-02T08:30:00+00:00"},
    {"title": "Old item - Dawn", "url": "https://www.dawn.com/a", "published": "2026-10-01T09:00:00+00:00"},
    {"title": "Newest item - Dawn", "url": "https://www.dawn.com/b", "published": "2026-10-02T08:00:00+00:00"},
    {"title": "Newest item - Dawn", "url": "https://www.dawn.com/b2", "published": "2026-10-02T07:00:00+00:00"},
]}


class Resp:
    def __init__(self, blob, ok=True):
        self.blob, self.ok = blob, ok

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError("404")

    def json(self):
        return self.blob


class FreshestTests(unittest.TestCase):
    def test_newer_remote_wins(self):
        newer = dict(RAW, fetched_at="2026-10-02T09:00:00+00:00")
        with mock.patch("news_feed.load_raw", return_value=(RAW, {"status": "ok"})):
            blob, where = news_desk.load_freshest(get=lambda *a, **k: Resp(newer))
        self.assertEqual(blob["fetched_at"], newer["fetched_at"])
        self.assertNotEqual(where, "bundled copy")

    def test_older_remote_loses(self):
        older = dict(RAW, fetched_at="2026-10-01T09:00:00+00:00")
        with mock.patch("news_feed.load_raw", return_value=(RAW, {"status": "ok"})):
            blob, where = news_desk.load_freshest(get=lambda *a, **k: Resp(older))
        self.assertEqual(blob["fetched_at"], RAW["fetched_at"])

    def test_remote_failure_falls_back_to_local(self):
        with mock.patch("news_feed.load_raw", return_value=(RAW, {"status": "ok"})):
            blob, where = news_desk.load_freshest(get=lambda *a, **k: Resp(None, ok=False))
        self.assertEqual(blob, RAW)


class DeskTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(news_desk.config, "NEWS_DISPLAY_PUBLISHERS", [])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_headlines_newest_first_and_deduped(self):
        raw = {"items": [dict(i, title=i["title"].replace("item", "oil price item")) for i in RAW["items"]]}
        heads = news_desk.market_headlines(raw, stocks=[])
        self.assertEqual([h["title"] for h in heads], ["Newest oil price item", "Old oil price item"])

    def test_off_topic_untagged_headline_is_left_off_the_desk(self):
        titles = [h["title"] for h in news_desk.market_headlines(RAW, stocks=[])]
        self.assertNotIn("Ronaldo-less Portugal win", titles)

    def test_stock_tagged_headlines_come_first(self):
        raw = {"items": [{"title": "Rupee steady - Dawn", "published": "2026-10-02T09:00"},
                         {"title": "PSO profit up - Dawn", "published": "2026-10-02T08:00"}]}
        with mock.patch.object(news_desk.config, "headline_matches_company",
                               side_effect=lambda s, t, *_: "PSO" in (t or "")):
            heads = news_desk.market_headlines(raw, stocks=["PSO"])
        self.assertEqual(heads[0]["stocks"], ["PSO"])

    def test_stale_review_named_not_shown(self):
        reviews = [("Claude", {"PSO": {"rating": "negative", "reason": "Fine", "sources": []}},
                    {"status": "ok"}),
                   ("Codex", {}, {"status": "stale"})]
        page = news_desk.desk_html(RAW, "main", reviews)
        self.assertIn("PSO", page)
        self.assertIn("Not current: Codex", page)
        self.assertIn("PSO", page)

    def test_headline_text_is_escaped(self):
        raw = {"items": [{"title": "<script>x</script>", "url": "javascript:alert(1)",
                          "published": "2026-10-02"}]}
        page = news_desk.desk_html(raw, "main", [])
        self.assertNotIn("<script>", page)
        self.assertNotIn("javascript:", page)


class CardTests(unittest.TestCase):
    def test_no_headlines_is_said_plainly(self):
        self.assertIn("No headlines", opportunity_cards.headlines_html([]))
        self.assertEqual(opportunity_cards.headlines_html(None), "")

    def test_badges_distinguish_stale_from_unreviewed(self):
        tags = opportunity_cards.news_tags("PSO", [("Claude", {}, {"status": "ok"}),
                                                  ("Codex", {}, {"status": "stale"})])
        self.assertEqual(tags, ["Claude: not reviewed", "Codex: review out of date"])



class AnchorTests(unittest.TestCase):
    def test_every_tracked_stock_has_name_anchors(self):
        missing = [s for s in news_desk.config.STOCKS if s not in news_desk.config.COMPANY_NEWS_ANCHORS]
        self.assertEqual(missing, [])

    def test_generic_power_news_is_not_power_cement(self):
        m = news_desk.config.headline_matches_company
        self.assertFalse(m("POWER", "Houthi attack on Madinah power station"))
        self.assertTrue(m("POWER", "Power Cement posts quarterly profit"))


class LiveRatingsTests(unittest.TestCase):
    """news_feed reads the newer ratings copy only when the dashboard asks."""
    STALE = {"as_of": "2026-10-01T09:00:00Z", "provider": "p", "model": "m", "ratings": {"PSO": {}}}
    FRESH = {"as_of": "2026-10-07T09:00:00Z", "provider": "p", "model": "m", "ratings": {"HUBC": {}}}

    def setUp(self):
        import json, os, tempfile
        import news_feed
        self.news_feed = news_feed
        self.dir = tempfile.mkdtemp()
        with open(os.path.join(self.dir, "news_ai_ratings.json"), "w") as fh:
            json.dump(self.STALE, fh)
        for patcher in (mock.patch.object(news_feed.config, "BASE_DIR", self.dir),
                        mock.patch.object(news_feed, "PREFER_LIVE_RATINGS", False)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_engine_default_never_fetches(self):
        with mock.patch("remote_data.fetch_json", side_effect=AssertionError("network")):
            raw = self.news_feed._read_rating_json("news_ai_ratings.json")
        self.assertEqual(raw["as_of"], self.STALE["as_of"])

    def test_dashboard_uses_newer_main_copy(self):
        self.news_feed.PREFER_LIVE_RATINGS = True
        with mock.patch("remote_data.fetch_json", return_value=self.FRESH):
            raw = self.news_feed._read_rating_json("news_ai_ratings.json")
        self.assertEqual(raw["as_of"], self.FRESH["as_of"])

    def test_dashboard_keeps_local_when_main_unreachable(self):
        self.news_feed.PREFER_LIVE_RATINGS = True
        with mock.patch("remote_data.fetch_json", return_value=None):
            raw = self.news_feed._read_rating_json("news_ai_ratings.json")
        self.assertEqual(raw["as_of"], self.STALE["as_of"])


if __name__ == "__main__":
    unittest.main()
