"""Tray app: Claude Code usage meter + "done / needs you" notifications.

    python -m usage_tray            run in the tray
"""
import ctypes
import json
import locale
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pystray
from PIL import Image, ImageDraw, ImageFont

from . import hooks, usage

APP = 'Usage Tray'
VERSION = '0.1.0'
DIR = os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), 'UsageTray')
F_SET = os.path.join(DIR, 'settings.json')
DEFAULTS = {'limit': None, 'cache_read': False, 'alert_pct': 85, 'notify_done': True, 'notify_ask': True,
            'ntfy_topic': '', 'lang': ''}

T = {
    'en': {
        'block': '5h block: {pct} · {used} / {cap}{auto}', 'block_idle': '5h block: idle',
        'resets': 'Resets at {t}', 'hits': 'At this pace you hit the limit at {t}',
        'today': 'Today {today} · last 7 days {week}', 'auto': ' (auto)',
        'limit': 'Block limit', 'limit_auto': 'Auto — your biggest block of the last 8 days ({n})',
        'limit_set': 'Set a number…', 'limit_ask': 'Tokens per 5-hour block (empty = auto):',
        'alert': 'Warn at {p}% of the block', 'cache': 'Count cache reads',
        'notify': 'Notifications', 'n_done': 'When Claude Code finishes a turn', 'n_ask': 'When Claude Code needs you',
        'hooks_on': 'Connect to Claude Code (add hooks)', 'hooks_off': 'Disconnect from Claude Code (remove hooks)',
        'phone': 'Phone (ntfy topic)…', 'phone_ask': 'ntfy.sh topic for phone notifications (empty = off).\n'
        'Install the ntfy app and subscribe to the same topic. Pick a long, hard-to-guess name.',
        'startup': 'Start with Windows', 'refresh': 'Refresh now', 'quit': 'Quit',
        'warn_t': 'Claude Code usage {p}%', 'warn_b': '{used} of ~{cap} tokens in this 5-hour block. Resets at {t}.',
        'done_t': 'Done — {proj}', 'ask_t': 'Claude Code needs you — {proj}',
        'hooks_ok': 'Hooks added. New Claude Code sessions will notify you.', 'hooks_gone': 'Hooks removed.',
        'hooks_err': 'Could not change ~/.claude/settings.json: {e}', 'running': 'Already running in the tray.',
        'about': 'Estimate from local Claude Code logs — nothing is sent anywhere.',
    },
    'ko': {
        'block': '5시간 구간 {pct} · {used} / {cap}{auto}', 'block_idle': '5시간 구간: 쉬는 중',
        'resets': '{t} 에 새 구간', 'hits': '이 속도면 {t} 에 한도',
        'today': '오늘 {today} · 최근 7일 {week}', 'auto': ' (자동)',
        'limit': '구간 한도', 'limit_auto': '자동 — 최근 8일 중 가장 많이 쓴 구간({n})',
        'limit_set': '숫자로 정하기…', 'limit_ask': '5시간 구간마다 쓸 토큰 수(비우면 자동):',
        'alert': '구간의 {p}% 에서 알림', 'cache': '캐시 읽기도 세기',
        'notify': '알림', 'n_done': 'Claude Code 가 답을 끝내면', 'n_ask': 'Claude Code 가 나를 기다리면(권한 요청 등)',
        'hooks_on': 'Claude Code 와 연결(훅 추가)', 'hooks_off': 'Claude Code 연결 끊기(훅 제거)',
        'phone': '폰 알림(ntfy 주제)…', 'phone_ask': '폰 알림에 쓸 ntfy.sh 주제(비우면 끔).\n'
        'ntfy 앱을 깔고 같은 주제를 구독하세요. 남이 못 맞힐 긴 이름으로.',
        'startup': 'Windows 시작할 때 켜기', 'refresh': '지금 새로 읽기', 'quit': '끝내기',
        'warn_t': 'Claude Code 사용량 {p}%', 'warn_b': '이번 5시간 구간에 {used} / 약 {cap} 토큰. {t} 에 새 구간.',
        'done_t': '끝남 — {proj}', 'ask_t': 'Claude Code 가 기다려요 — {proj}',
        'hooks_ok': '훅을 넣었어요. 새로 여는 Claude Code 세션부터 알려 줍니다.', 'hooks_gone': '훅을 뺐어요.',
        'hooks_err': '~/.claude/settings.json 을 못 바꿨어요: {e}', 'running': '이미 트레이에서 돌고 있어요.',
        'about': '이 PC 의 Claude Code 기록으로 낸 추정치 — 어디로도 보내지 않습니다.',
    },
}


# ---------------------------------------------------------------- settings
def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(F_SET, encoding='utf-8') as f:
            s.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return s


def save_settings(s):
    os.makedirs(DIR, exist_ok=True)
    tmp = F_SET + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(s, f, ensure_ascii=False, indent=2)
    os.replace(tmp, F_SET)


def lang_of(s):
    if s.get('lang') in T:
        return s['lang']
    try:
        loc = locale.getdefaultlocale()[0] or ''
    except ValueError:
        loc = ''
    if sys.platform == 'win32':
        try:
            loc = locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), loc)
        except Exception:
            pass
    return 'ko' if loc.lower().startswith('ko') else 'en'


# ---------------------------------------------------------------- icon
def _font(px):
    for name in ('segoeuib.ttf', 'arialbd.ttf', 'DejaVuSans-Bold.ttf', 'Arial Bold.ttf'):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    return ImageFont.load_default()


def color_for(pct, alert):
    if pct is None:
        return (106, 97, 87)          # no estimate yet — grey
    if pct >= 100:
        return (180, 35, 24)          # red
    if pct >= alert:
        return (154, 63, 29)          # brick
    if pct >= alert - 15:
        return (138, 90, 18)          # ochre
    return (43, 39, 35)               # ink


def render_icon(pct, alert=85, size=64):
    """Square with the block percentage. Under it, a thin bar fills with the same number."""
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    bg = color_for(pct, alert)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 8, fill=bg + (255,))
    txt = '–' if pct is None else ('!!' if pct >= 100 else str(int(pct)))
    f = _font(int(size * (0.62 if len(txt) < 3 else 0.5)))
    box = d.textbbox((0, 0), txt, font=f)
    w, h = box[2] - box[0], box[3] - box[1]
    d.text(((size - w) / 2 - box[0], (size * 0.86 - h) / 2 - box[1]), txt, font=f, fill=(255, 250, 242, 255))
    if pct is not None:
        bar = int((size - 8) * min(pct, 100) / 100)
        d.rectangle((4, size - 9, size - 5, size - 5), fill=(255, 250, 242, 90))
        if bar:
            d.rectangle((4, size - 9, 4 + bar, size - 5), fill=(255, 250, 242, 255))
    return img


# ---------------------------------------------------------------- small helpers
def hhmm(t):
    return time.strftime('%H:%M', time.localtime(t)) if t else '-'


def last_reply(transcript, limit=120):
    """Last assistant text in a transcript (reads only the tail)."""
    try:
        with open(transcript, 'rb') as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 262144))
            lines = f.read().split(b'\n')
    except (OSError, TypeError):
        return ''
    for raw in reversed(lines):
        if b'"assistant"' not in raw:
            continue
        try:
            o = json.loads(raw)
        except ValueError:
            continue
        for c in reversed((o.get('message') or {}).get('content') or []):
            if isinstance(c, dict) and c.get('type') == 'text' and c.get('text', '').strip():
                t = ' '.join(c['text'].split())
                return t if len(t) <= limit else t[:limit - 1] + '…'
    return ''


def ntfy(topic, title, body):
    if not topic:
        return
    try:
        try:
            import truststore      # company networks that inspect TLS
            truststore.inject_into_ssl()
        except ImportError:
            pass
        req = urllib.request.Request('https://ntfy.sh/' + urllib.request.quote(topic, safe=''),
                                     data=body.encode('utf-8'), method='POST',
                                     headers={'Title': title.encode('utf-8').decode('latin-1'), 'Tags': 'robot'})
        urllib.request.urlopen(req, timeout=8).close()
    except Exception:
        pass


RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'


def startup_cmd():
    if getattr(sys, 'frozen', False):
        return '"%s"' % sys.executable
    exe = sys.executable.replace('python.exe', 'pythonw.exe')
    return '"%s" -m usage_tray' % exe


def startup_get():
    if sys.platform != 'win32':
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, 'UsageTray')
            return True
    except OSError:
        return False


def startup_set(on):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, 'UsageTray', 0, winreg.REG_SZ, startup_cmd())
        else:
            try:
                winreg.DeleteValue(k, 'UsageTray')
            except OSError:
                pass


def ask_text(title, prompt, initial=''):
    """Tiny input box on its own thread-local Tk root. Returns None on cancel."""
    import tkinter as tk
    from tkinter import simpledialog
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    try:
        return simpledialog.askstring(title, prompt, initialvalue=initial, parent=root)
    finally:
        root.destroy()


# ---------------------------------------------------------------- app
class App:
    def __init__(self):
        self.s = load_settings()
        self.t = T[lang_of(self.s)]
        self.reader = usage.Reader()
        self.sum = None
        self.warned = {}               # block start -> highest level already warned
        self.wake = threading.Event()
        self.icon = pystray.Icon('UsageTray', render_icon(None), APP, menu=self.menu())

    # --- menu
    def menu(self):
        t, M, S = self.t, pystray.MenuItem, pystray.Menu.SEPARATOR
        def line(fn):
            return M(lambda _i: fn(), None, enabled=False)
        def setting(key):
            def flip(_i, _it):
                self.s[key] = not self.s[key]
                save_settings(self.s)
                self.wake.set()
            return flip
        return pystray.Menu(
            line(self.l_block), line(self.l_when), line(self.l_today), S,
            M(t['limit'], pystray.Menu(
                M(lambda _i: t['limit_auto'].format(n=usage.short((self.sum or {}).get('auto_limit'))),
                  self.limit_auto, checked=lambda _i: not self.s['limit'], radio=True),
                M(t['limit_set'], self.limit_ask, checked=lambda _i: bool(self.s['limit']), radio=True),
                S,
                M(t['cache'], setting('cache_read'), checked=lambda _i: self.s['cache_read']),
                *[M(t['alert'].format(p=p), self.alert_to(p), checked=(lambda p: lambda _i: self.s['alert_pct'] == p)(p),
                    radio=True) for p in (75, 85, 95)])),
            M(t['notify'], pystray.Menu(
                M(t['n_done'], setting('notify_done'), checked=lambda _i: self.s['notify_done']),
                M(t['n_ask'], setting('notify_ask'), checked=lambda _i: self.s['notify_ask']),
                S,
                M(lambda _i: t['hooks_off'] if hooks.installed() else t['hooks_on'], self.toggle_hooks),
                M(t['phone'], self.phone_ask))),
            M(t['startup'], self.toggle_startup, checked=lambda _i: startup_get(), visible=sys.platform == 'win32'),
            M(t['refresh'], lambda _i, _it: self.wake.set()),
            S, line(lambda: t['about']), M(t['quit'], self.quit))

    def l_block(self):
        s = self.sum
        if not s:
            return '…'
        if not s['active']:
            return self.t['block_idle']
        return self.t['block'].format(pct='-' if s['pct'] is None else '%d%%' % s['pct'], used=usage.short(s['block_used']),
                                      cap=usage.short(s['limit']), auto=self.t['auto'] if s['limit_is_auto'] else '')

    def l_when(self):
        s = self.sum or {}
        if not s.get('active'):
            return '—'
        txt = self.t['resets'].format(t=hhmm(s['resets_at']))
        if s.get('hits_limit_at') and s['hits_limit_at'] < s['resets_at']:
            txt += ' · ' + self.t['hits'].format(t=hhmm(s['hits_limit_at']))
        return txt

    def l_today(self):
        s = self.sum or {}
        return self.t['today'].format(today=usage.short(s.get('today')), week=usage.short(s.get('week')))

    # --- menu actions
    def limit_auto(self, _i, _it):
        self.s['limit'] = None
        save_settings(self.s)
        self.wake.set()

    def limit_ask(self, _i, _it):
        def run():
            cur = str(self.s['limit'] or '')
            v = ask_text(APP, self.t['limit_ask'], cur)
            if v is None:
                return
            v = v.replace(',', '').replace('_', '').strip().upper()
            mul = 1
            if v[-1:] in ('K', 'M'):
                mul, v = (1000 if v[-1] == 'K' else 1000000), v[:-1]
            try:
                n = int(float(v) * mul) if v else None
            except ValueError:
                return
            self.s['limit'] = n if n and n > 0 else None
            save_settings(self.s)
            self.wake.set()
        threading.Thread(target=run, daemon=True).start()

    def alert_to(self, p):
        def f(_i, _it):
            self.s['alert_pct'] = p
            save_settings(self.s)
            self.wake.set()
        return f

    def phone_ask(self, _i, _it):
        def run():
            v = ask_text(APP, self.t['phone_ask'], self.s['ntfy_topic'])
            if v is not None:
                self.s['ntfy_topic'] = v.strip()
                save_settings(self.s)
        threading.Thread(target=run, daemon=True).start()

    def toggle_hooks(self, _i, _it):
        try:
            if hooks.installed():
                hooks.uninstall()
                self.toast(APP, self.t['hooks_gone'])
            else:
                hooks.install()
                self.toast(APP, self.t['hooks_ok'])
        except Exception as e:
            self.toast(APP, self.t['hooks_err'].format(e=e))

    def toggle_startup(self, _i, _it):
        startup_set(not startup_get())

    def quit(self, _i, _it):
        self._stopped = True
        self.wake.set()
        self.icon.stop()

    # --- notifications
    def toast(self, title, body, phone=False):
        try:
            self.icon.notify(body or ' ', title)
        except Exception:
            pass
        if phone:
            threading.Thread(target=ntfy, args=(self.s['ntfy_topic'], title, body), daemon=True).start()

    def on_hook(self, event, data):
        proj = os.path.basename(str(data.get('cwd') or '').rstrip('\\/')) or 'Claude Code'
        if event == 'stop' and self.s['notify_done']:
            self.toast(self.t['done_t'].format(proj=proj), last_reply(data.get('transcript_path')), phone=True)
        elif event == 'notification' and self.s['notify_ask']:
            self.toast(self.t['ask_t'].format(proj=proj), str(data.get('message') or ''), phone=True)

    def check_alert(self, s):
        if not s['active'] or s['pct'] is None:
            return
        start = s['resets_at'] - usage.BLOCK
        lvl = 100 if s['pct'] >= 100 else (self.s['alert_pct'] if s['pct'] >= self.s['alert_pct'] else 0)
        if lvl and self.warned.get(start, 0) < lvl:
            self.warned = {start: lvl}
            self.toast(self.t['warn_t'].format(p=s['pct']),
                       self.t['warn_b'].format(used=usage.short(s['block_used']), cap=usage.short(s['limit']),
                                               t=hhmm(s['resets_at'])), phone=True)

    # --- loops
    def loop(self):
        while not self._stopped:
            try:
                recs = self.reader.refresh()
                s = usage.summary(recs, self.s['limit'], self.s['cache_read'])
                self.sum = s
                self.icon.icon = render_icon(s['pct'] if s['active'] else (0 if s['limit'] else None), self.s['alert_pct'])
                tip = self.l_block() + '\n' + self.l_today()
                self.icon.title = tip[:127]
                self.icon.update_menu()
                self.check_alert(s)
                self.last_ok = (time.time(), tip)
            except Exception as e:      # keep the meter alive; show the problem in the tooltip and error.log
                self.icon.title = ('%s: %s' % (APP, e))[:127]
                try:
                    import traceback
                    os.makedirs(DIR, exist_ok=True)
                    with open(os.path.join(DIR, 'error.log'), 'a', encoding='utf-8') as f:
                        f.write(time.strftime('%Y-%m-%d %H:%M:%S ') + traceback.format_exc() + '\n')
                except OSError:
                    pass
            if self.wake.wait(60):
                self.wake.clear()

    _stopped = False

    def serve(self):
        app = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = min(int(self.headers.get('Content-Length') or 0), 1 << 20)
                try:
                    data = json.loads(self.rfile.read(n) or b'{}')
                except ValueError:
                    data = {}
                ev = self.path.strip('/').split('/')[-1]
                self.send_response(204)
                self.end_headers()
                if isinstance(data, dict) and ev in ('stop', 'notification'):
                    app.on_hook(ev, data)

            def log_message(self, *a):
                pass

        self.httpd.RequestHandlerClass = H
        self.httpd.serve_forever()

    def run(self):
        try:
            self.httpd = ThreadingHTTPServer(('127.0.0.1', hooks.PORT), BaseHTTPRequestHandler)
        except OSError:                 # port taken — most likely we are already running
            print(self.t['running'])
            return 1
        threading.Thread(target=self.serve, daemon=True).start()

        def setup(icon):
            icon.visible = True
            threading.Thread(target=self.loop, daemon=True).start()
        self.icon.run(setup)
        self._stopped = True
        self.httpd.shutdown()
        return 0


def main():
    if sys.platform == 'win32':
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
    return App().run()


if __name__ == '__main__':
    sys.exit(main())
