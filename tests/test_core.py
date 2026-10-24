import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import nse_core as C  # noqa: E402


def leg(strike, oi, chg):
    return dict(strikePrice=strike, openInterest=oi, changeinOpenInterest=chg, totalTradedVolume=1,
                impliedVolatility=1.0, lastPrice=1.0, change=0.0, buyQuantity1=1, buyPrice1=1.0,
                sellPrice1=1.0, sellQuantity1=1, underlyingValue=100.5)


def chain(n=12):
    data = [{'expiryDates': '28-Oct-2026', 'CE': leg(100 + i * 50, 1000 * (i + 1), 100 * i - 300),
             'PE': leg(100 + i * 50, 1000 * (n - i), -50 * i + 200)} for i in range(n)]
    return {'records': {'timestamp': '23-Oct-2026 11:05:00', 'data': data}}


class CoreTests(unittest.TestCase):
    def test_parse_and_analyze(self):
        rows, ts, pts = C.parse_chain(chain(), '28-oct-2026')
        self.assertEqual((len(rows), len(rows[0]), ts, pts), (12, 21, '23-Oct-2026 11:05:00', 100.5))
        a = C.analyze(rows, 250, 'Index')
        self.assertEqual((a.max_call_oi_sp, a.max_put_oi_sp), (650.0, 100.0))
        self.assertEqual(a.put_call_ratio, 1.0)

    def test_errors(self):
        rows, _, _ = C.parse_chain(chain(), '28-Oct-2026')
        with self.assertRaises(C.IncorrectStrike):
            C.analyze(rows, 12345, 'Index')
        with self.assertRaises(C.InvalidExpiry):
            C.parse_chain(chain(), '01-Jan-2020')

    def test_labels_and_versions(self):
        self.assertEqual(C.itm_label(-1, 5), 'Yes')
        self.assertEqual(C.itm_label(10, 5), 'No')
        self.assertTrue(C.is_newer('v5.9'))
        self.assertFalse(C.is_newer('5.8'))


if __name__ == '__main__':
    unittest.main()
