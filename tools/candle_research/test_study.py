import importlib.util
import unittest
from pathlib import Path

import pandas as pd

spec = importlib.util.spec_from_file_location('candle_study', Path(__file__).with_name('study.py'))
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)


class CandleResearchTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame({'symbol': 'PSO', 'date': pd.bdate_range('2025-01-01', periods=70).strftime('%Y-%m-%d'),
            'open': 100., 'high': 102., 'low': 98., 'close': 101., 'volume': 100000., 'source': 'test'})

    def test_future_candles_cannot_change_previous_patterns(self):
        before, _ = study.features(self.frame.iloc[:60])
        modified = self.frame.copy()
        modified.loc[60:, ['open', 'high', 'low', 'close', 'volume']] = [1, 9000, 1, 8000, 1e9]
        after, _ = study.features(modified)
        pd.testing.assert_frame_equal(before, after.iloc[:60])

    def test_bad_candle_blocks_entire_context(self):
        self.frame.loc[45, 'high'] = 99
        flags, usable = study.features(self.frame)
        self.assertFalse(usable.iloc[45])
        self.assertFalse(flags.iloc[45:70].any().any())

    def test_next_open_and_incomplete_horizon(self):
        flags = pd.DataFrame(False, index=self.frame.index, columns=study.SPEC['patterns'])
        flags.loc[[45, 48, 69], 'bullish_engulfing'] = True
        self.frame.loc[46, 'open'] = 102
        _, usable = study.features(self.frame)
        rows = study.sample(self.frame, flags, usable, list(self.frame.date))
        self.assertEqual(len(rows), 2)  # continuing overlap is not a second trade
        self.assertAlmostEqual(rows[0]['gross_pct'], 100*(101/102-1))
        self.assertEqual(rows[1]['status'], 'unresolved')

    def test_partition_boundary_is_purged(self):
        self.frame['date'] = pd.bdate_range('2025-11-03', periods=70).strftime('%Y-%m-%d')
        flags = pd.DataFrame(False, index=self.frame.index, columns=study.SPEC['patterns'])
        flags.loc[42, 'bullish_engulfing'] = True
        _, usable = study.features(self.frame)
        rows = study.sample(self.frame, flags, usable, list(self.frame.date))
        self.assertEqual(rows[0]['status'], 'purged')

    def test_missing_market_session_blocks_a_breakout_context(self):
        self.frame.loc[65, ['open', 'high', 'low', 'close', 'volume']] = [101, 104, 100, 103, 200000]
        sessions = list(self.frame.date)
        before, _ = study.features(self.frame, sessions)
        self.assertTrue(before.loc[65, 'volume_breakout'])
        missing = self.frame.drop(index=50)
        after, _ = study.features(missing, sessions)
        self.assertFalse(after.loc[65, 'volume_breakout'])

    def test_incomplete_tail_still_counts_a_continuing_setup_once(self):
        flags = pd.DataFrame(False, index=self.frame.index, columns=study.SPEC['patterns'])
        flags.loc[[66, 69], 'bullish_engulfing'] = True
        _, usable = study.features(self.frame)
        rows = study.sample(self.frame, flags, usable, list(self.frame.date))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['decision_date'], self.frame.iloc[66].date)
        self.assertEqual(rows[0]['status'], 'unresolved')


if __name__ == '__main__':
    unittest.main()
