"""Self-tests for hobo.py using synthetic MX1101 images.

These check the decoder's plumbing (header groups, bit packing, event
encoding, timestamps, wrapped pages). They are built from the same reading of
HOBOware's code as the decoder, so they cannot catch a misreading of it:
compare against a real file exported from HOBOware for that.

    python -m unittest test_hobo -v
"""

import unittest
from datetime import datetime, timedelta, timezone

from cryojane_bench import hobo

TZ = timezone(timedelta(hours=-5))
LAUNCH = datetime(2026, 3, 1, 12, 0, 0, tzinfo=TZ)


def tag(tag_id, value=b''):
    assert len(value) < 0x80
    return tag_id.to_bytes(2, 'big') + bytes([len(value)]) + value


def u4(v):
    return (v & 0xFFFFFFFF).to_bytes(4, 'big')


def date(dt):
    return bytes([dt.year // 100, dt.year % 100, dt.month, dt.day,
                  dt.hour, dt.minute, dt.second, 0])


def channel_group(enabled):
    return (tag(0x8816) + tag(0x8888) + tag(0x880B, bytes([enabled]))
            + tag(0x880C, b'\0') + tag(0x8811))


def header(sod, interval=60, delay=0, wrap=False, options=None,
           enabled=(1, 1, 1), size=0x20000):
    body = (tag(0x880D, u4(sod)) + tag(0x881D, u4(sod - 3))
            + tag(0x8802, (9218).to_bytes(2, 'big'))
            + tag(0x8805, b'MX1101') + tag(0x8806, b'20123456')
            + tag(0x8807, date(LAUNCH)) + tag(0x8809, u4(delay))
            + tag(0x8808, u4(interval)) + tag(0x8812, u4(-5 * 3600))
            + tag(0x881F, u4(size)) + tag(0x8863, bytes([int(wrap)])))
    if options is not None:
        body += tag(0x88B0, bytes([options]))
    for e in enabled:
        body += channel_group(e)
    out = b'HOBO' + body + b'\xff\xff'
    assert len(out) <= sod
    return out + b'\xff' * (sod - len(out))


class Bits:
    def __init__(self):
        self.bits = []

    def put(self, value, n):
        self.bits += [(value >> (n - 1 - i)) & 1 for i in range(n)]

    def event(self, flag_bits, etype, offset=0, sub=None, data=None):
        """Phoenix event: flag, type, then (sub, tlen, t, dlen, d) or (len, t)."""
        self.put((1 << flag_bits) - 1, flag_bits)
        self.put(etype, 4)
        if etype == 15:
            return
        tn = (offset.bit_length() + 3) // 4
        if etype == 14:
            self.put(sub, 8)
        self.put(tn, 4)
        self.put(offset, tn * 4)
        if etype == 14:
            dn = 0 if data is None else max(1, (data.bit_length() + 3) // 4)
            self.put(dn, 4)
            self.put(data or 0, dn * 4)

    def tobytes(self, size=None):
        bits = self.bits + [1] * (-len(self.bits) % 8)
        out = bytes(int(''.join(map(str, bits[i:i + 8])), 2)
                    for i in range(0, len(bits), 8))
        if size is not None:
            out += b'\xff' * (size - len(out))
        return out


class HeaderTests(unittest.TestCase):
    def test_groups_and_lookup(self):
        tags, _ = hobo.parse_header(header(4096))
        h = hobo.Header(tags)
        self.assertEqual(h.int(0x8802), 9218)
        self.assertEqual(h.int(0x8812), -18000)
        self.assertEqual(len(h.all(0x8816)), 3)
        self.assertEqual(h.all(0x8816)[1].find(0x880B).as_int(), 1)
        self.assertEqual(h.date(0x8807, TZ), LAUNCH)

    def test_bits(self):
        self.assertEqual(hobo.get_bits(b'\xa5\x0f', 4, 8), 0x50)
        self.assertEqual(hobo.get_bits(b'\xa5\x0f', 0, 16), 0xA50F)


class StreamTests(unittest.TestCase):
    RAW = [(1900, 8000, 150), (1910, 8100, 150), (1920, 16383, 149)]

    def build(self, **kw):
        b = Bits()
        b.event(12, 14, offset=5, sub=1)          # Started, 5 s after launch
        for t, rh, bat in self.RAW:
            b.put(t, 12)
            b.put(rh, 14)
            b.put(bat, 8)
        b.event(12, 14, offset=30, sub=3)         # Button Up mid-interval
        b.put(1930, 12)
        b.put(8200, 14)
        b.put(149, 8)
        b.event(12, 15)
        return header(4096, **kw) + b.tobytes(4096)

    def test_samples_events_times(self):
        hf = hobo.decode(self.build(interval=60, delay=0))
        self.assertEqual(hf.model, 'MX1101')
        self.assertEqual([c.key for c in hf.channels],
                         ['temp', 'rh', 'battery', 'dewpoint'])
        self.assertEqual(len(hf.samples), 4)
        t0 = LAUNCH + timedelta(seconds=5)
        self.assertEqual([s.time for s in hf.samples],
                         [t0 + timedelta(seconds=60 * i) for i in range(4)])
        s = hf.samples[0].values
        self.assertAlmostEqual(s['temp'], hobo.temp_thermistor([1900]))
        self.assertAlmostEqual(s['rh'], 8000 / 16384 * 100, places=3)
        self.assertAlmostEqual(s['battery'], 2.93)
        self.assertEqual(hf.samples[2].values['rh'], 100.0)
        self.assertAlmostEqual(
            s['dewpoint'], hobo.dewpoint_c(hobo.thermistor_c(1900),
                                           8000 / 16384 * 100), places=3)
        names = [e.name for e in hf.events]
        self.assertEqual(names, ['Started', 'Button Up', 'End Of File'])
        self.assertEqual(hf.events[1].time,
                         t0 + timedelta(seconds=120 + 30))

    def test_temperature_curve(self):
        # 10k thermistor: raw at half scale is 10 kOhm, i.e. ~25 C
        self.assertAlmostEqual(hobo.thermistor_c(2047.5), 25.0, delta=0.05)
        self.assertGreater(hobo.thermistor_c(1000), hobo.thermistor_c(3000))

    def test_disabled_channel_changes_packing(self):
        b = Bits()
        b.event(14, 14, offset=0, sub=1)          # RH is now the first field
        b.put(8000, 14)
        b.event(14, 15)
        hf = hobo.decode(header(4096, enabled=(0, 1, 0)) + b.tobytes(4096))
        self.assertEqual([c.key for c in hf.channels], ['rh'])
        self.assertEqual(len(hf.samples), 1)
        self.assertAlmostEqual(hf.samples[0].values['rh'],
                               8000 / 16384 * 100, places=3)

    def test_statistics_channels(self):
        b = Bits()
        b.event(12, 14, offset=0, sub=1)
        # temp, temp max, avg, sd | rh, rh max, avg, sd | battery
        for v, n in ((2000, 12), (1990, 12), (2001, 12), (10, 12),
                     (8000, 14), (8100, 14), (8050, 14), (100, 14),
                     (150, 8)):
            b.put(v, n)
        b.event(12, 15)
        opts = 1 | 2 | 4                          # sd, avg, max
        hf = hobo.decode(header(4096, options=opts) + b.tobytes(4096))
        keys = [c.key for c in hf.channels]
        self.assertEqual(keys, ['temp', 'temp_max', 'temp_avg', 'temp_sd',
                                'rh', 'rh_max', 'rh_avg', 'rh_sd',
                                'battery', 'dewpoint'])
        v = hf.samples[0].values
        self.assertAlmostEqual(v['temp_sd'],
                               hobo.temp_thermistor_sd([2001, 10]))
        self.assertAlmostEqual(v['rh_sd'], 100 / 16384 * 100, places=3)


class WrapTests(unittest.TestCase):
    PAGE = 4096

    def page(self, block, seconds, raws, eof=False):
        b = Bits()
        b.event(12, 14, offset=seconds, sub=1)    # seconds since launch
        b.event(12, 14, sub=16, data=60)          # interval
        b.event(12, 14, sub=17, data=block)       # block count
        b.event(12, 14, sub=19)                   # BEH end
        for t in raws:
            b.put(t, 12)
            b.put(8000, 14)
            b.put(150, 8)
        if eof:
            b.event(12, 15)
        return b.tobytes(self.PAGE)

    def test_wrapped_pages_reordered(self):
        # 3 data pages; the oldest surviving block (5) sits in page 2.
        img = (header(self.PAGE, wrap=True, size=4 * self.PAGE)
               + self.page(7, 7200, [2010, 2011], eof=True)
               + self.page(5, 3600, [2000, 2001])
               + self.page(6, 5400, [2005]))
        hf = hobo.decode(img)
        raws = [round(s.values['temp'], 3) for s in hf.samples]
        expect = [round(hobo.temp_thermistor([r]), 3)
                  for r in (2000, 2001, 2005, 2010, 2011)]
        self.assertEqual(raws, expect)
        # first sample of the oldest block is at launch + 3600 s
        self.assertEqual(hf.samples[0].time,
                         LAUNCH + timedelta(seconds=3600))
        self.assertEqual(hf.events[-1].name, 'End Of File')


if __name__ == '__main__':
    unittest.main()
