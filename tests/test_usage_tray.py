import json
import os
import shutil
import tempfile
import time
import unittest

from usage_tray import hooks, usage


def line(ts, mid, req, i=100, o=50, cw=0, cr=0, model='claude-x', typ='assistant'):
    iso = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(ts)) + '.123Z'
    return json.dumps({'type': typ, 'timestamp': iso, 'requestId': req,
                       'message': {'id': mid, 'model': model, 'usage': {
                           'input_tokens': i, 'output_tokens': o,
                           'cache_creation_input_tokens': cw, 'cache_read_input_tokens': cr}}})


class Parse(unittest.TestCase):
    def test_line(self):
        k, ts, u = usage.parse_line(line(1_800_000_000, 'm1', 'r1', 1, 2, 3, 4).encode())
        self.assertEqual((k, u), ('m1|r1', [1, 2, 3, 4]))
        self.assertAlmostEqual(ts, 1_800_000_000.123, places=3)

    def test_skips(self):
        for raw in (line(1e9, 'm', 'r', model='<synthetic>'), line(1e9, 'm', 'r', typ='user'),
                    line(1e9, 'm', 'r', 0, 0), '{broken', ''):
            self.assertIsNone(usage.parse_line(raw.encode()))

    def test_short(self):
        self.assertEqual([usage.short(n) for n in (5, 1200, 2_000_000, 12_345_678)], ['5', '1.2K', '2M', '12.3M'])


class Blocks(unittest.TestCase):
    H = 3600

    def test_block_rules(self):
        t0 = 1_800_000_000 - 1_800_000_000 % self.H + 600          # 10 min past an hour
        recs = [(t0, [10, 0, 0, 0]), (t0 + 4 * self.H, [10, 0, 0, 0]),   # same block
                (t0 + 5 * self.H, [10, 0, 0, 0]),                        # past start+5h -> new
                (t0 + 11 * self.H, [10, 0, 0, 0])]                       # 6h gap -> new
        bl = usage.blocks(recs)
        self.assertEqual(len(bl), 3)
        self.assertEqual(bl[0][0], t0 - 600)                             # floored to the hour
        self.assertEqual(bl[0][2][0], 20)

    def test_summary_auto_limit_and_pct(self):
        now = 1_800_000_000
        recs = [(now - 30 * self.H, [800, 200, 0, 0]),                 # finished block: 1000
                (now - 600, [400, 100, 0, 9999])]                     # current: 500 (cache read ignored)
        s = usage.summary(recs, now=now)
        self.assertTrue(s['active'])
        self.assertEqual((s['limit'], s['block_used'], s['pct'], s['limit_is_auto']), (1000, 500, 50, True))
        s = usage.summary(recs, limit=2000, now=now)
        self.assertEqual((s['pct'], s['limit_is_auto']), (25, False))
        self.assertEqual(usage.summary(recs, cache_read=True, now=now)['block_used'], 500 + 9999)

    def test_idle(self):
        now = 1_800_000_000
        s = usage.summary([(now - 6 * self.H, [5, 5, 0, 0])], now=now)
        self.assertFalse(s['active'])
        self.assertIsNone(s['resets_at'])


class Reader(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)

    def test_incremental_and_dedupe(self):
        now = time.time()
        a = os.path.join(self.d, 'p1', 'a.jsonl')
        os.makedirs(os.path.dirname(a))
        with open(a, 'w') as f:
            f.write(line(now - 60, 'm1', 'r1', 10, 1) + '\n' + line(now - 50, 'm1', 'r1', 10, 5) + '\n')
        r = usage.Reader(self.d)
        recs = r.refresh()
        self.assertEqual([u for _t, u in recs], [[10, 5, 0, 0]])           # streamed reply: last line wins
        with open(a, 'a') as f:
            f.write(line(now - 40, 'm2', 'r2', 7, 7) + '\n' + line(now - 30, 'm3', 'r3'))   # last line unfinished
        self.assertEqual(len(r.refresh()), 2)
        b = os.path.join(self.d, 'p2', 'b.jsonl')                            # resumed session copies m1
        os.makedirs(os.path.dirname(b))
        with open(b, 'w') as f:
            f.write(line(now - 50, 'm1', 'r1', 10, 5) + '\n')
        self.assertEqual(len(r.refresh()), 2)


class Hooks(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.p = os.path.join(self.d, 'settings.json')

    def test_install_keeps_others_and_uninstall_restores(self):
        mine = {'model': 'x', 'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'echo hi'}]}],
                                        'PreToolUse': [{'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': 'g'}]}]}}
        with open(self.p, 'w') as f:
            json.dump(mine, f)
        self.assertFalse(hooks.installed(self.p))
        hooks.install(self.p)
        hooks.install(self.p)                                              # twice — still one entry each
        self.assertTrue(hooks.installed(self.p))
        d = json.load(open(self.p))
        self.assertEqual(len(d['hooks']['Stop']), 2)
        self.assertEqual(len(d['hooks']['Notification']), 1)
        self.assertIn('/stop', d['hooks']['Stop'][1]['hooks'][0]['command'])
        self.assertTrue(os.path.exists(self.p + '.usage-tray-backup'))
        hooks.uninstall(self.p)
        self.assertEqual(json.load(open(self.p)), mine)

    def test_missing_file_and_broken_file(self):
        hooks.install(self.p)
        self.assertTrue(hooks.installed(self.p))
        hooks.uninstall(self.p)
        self.assertEqual(json.load(open(self.p)), {})
        with open(self.p, 'w') as f:
            f.write('{not json')
        self.assertRaises(ValueError, hooks.install, self.p)
        self.assertEqual(open(self.p).read(), '{not json')                 # never overwritten


class Icon(unittest.TestCase):
    def test_render(self):
        from usage_tray import app
        for p in (None, 0, 50, 99, 140):
            self.assertEqual(app.render_icon(p).size, (64, 64))
        self.assertNotEqual(app.color_for(90, 85), app.color_for(10, 85))

    def test_last_reply(self):
        from usage_tray import app
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        p = os.path.join(d, 't.jsonl')
        with open(p, 'w', encoding='utf-8') as f:
            f.write(json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': '첫 답'}]}}) + '\n')
            f.write(json.dumps({'type': 'assistant', 'message': {'content': [
                {'type': 'text', 'text': '마지막\n  답입니다'}, {'type': 'tool_use'}]}}) + '\n')
        self.assertEqual(app.last_reply(p), '마지막 답입니다')
        self.assertEqual(app.last_reply(os.path.join(d, 'none')), '')


if __name__ == '__main__':
    unittest.main()
