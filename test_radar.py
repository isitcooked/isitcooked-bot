import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bot


class RadarTests(unittest.TestCase):
    def good_snapshot(self):
        return {"age": 8, "liq": 80000, "v1": 90000, "v5": 15000, "v24": 400000,
                "buys": 150, "sells": 70, "c1": 24, "c5": 4,
                "websites": ["https://example.com"], "socials": ["https://x.com/example"]}

    def test_promising_market_passes(self):
        snapshot = self.good_snapshot()
        self.assertTrue(bot.qualifies(snapshot))
        self.assertGreaterEqual(bot.market_score(snapshot), bot.MIN_SCORE)

    def test_weak_or_suspicious_markets_fail(self):
        base = self.good_snapshot()
        for change in ({"liq": 5000}, {"v1": 1000}, {"buys": 40, "sells": 80},
                       {"c1": -5}, {"age": 200}, {"websites": [], "socials": []}):
            with self.subTest(change=change):
                self.assertFalse(bot.qualifies(base | change))

    def test_atomic_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bot, "STATE", Path(directory)):
            bot.save("sample", {"ok": True})
            self.assertEqual(bot.load("sample", {}), {"ok": True})
            self.assertFalse((Path(directory) / "sample.json.tmp").exists())

    def test_logo_hosts_are_restricted(self):
        self.assertEqual(bot.safe_logo_url("https://evil.example/logo.png"), "")
        self.assertTrue(bot.safe_logo_url("https://cdn.dexscreener.com/cms/images/example.png"))

    def test_caption_is_factual_and_has_disclaimer(self):
        data = {"token": {"symbol":"TEST", "chain":"solana", "chain_name":"Solana", "address":"A"*32},
                "buys":100, "sells":50, "radar_score":82, "liquidity":75000, "volume":100000,
                "change_h1":25, "risk_list":[]}
        text = bot.caption(data)
        self.assertIn("Radar score: 82/100", text)
        self.assertIn("Not financial advice", text)
        self.assertIn("A"*32, text)


if __name__ == "__main__":
    unittest.main()
