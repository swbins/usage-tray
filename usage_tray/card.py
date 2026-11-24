"""Usage card: a small panel that opens next to the tray icon on left-click.

Plain tkinter (ships with Python), drawn on one Canvas in the swbins "paper notebook" look:
flat paper, ink rules, square corners, a spine on the right edge. Follows the Windows
light/dark app setting. It only reads numbers the tray already has; settings stay in the menu.
"""
import ctypes
import sys
import threading
import time


def level(pct, alert):
    """none / ok / near / warn / over — the same steps as the tray icon colours."""
    if pct is None:
        return 'none'
    if pct >= 100:
        return 'over'
    if pct >= alert:
        return 'warn'
    if pct >= alert - 15:
        return 'near'
    return 'ok'


# Tokens from the site's spine.css (light and dark), plus the four meter steps.
PALETTE = {
    'light': dict(bg='#EEF3EA', ink='#2A3324', sub='#5A6650', spine='#2F3A3A', on_spine='#E9EEE2',
                  face='#E2E8D8', line='#D6DCCB', track='#D6DCCB', edge='#2F3A3A',
                  none='#5A6650', ok='#2F3A3A', near='#8A6418', warn='#9A3F1D', over='#B42318'),
    'dark': dict(bg='#1B2018', ink='#DEE5D3', sub='#A4AE97', spine='#5A6D69', on_spine='#EEF2E8',
                 face='#2C3527', line='#2F382A', track='#3A4433', edge='#A4AE97',
                 none='#A4AE97', ok='#B5C4AE', near='#D9A845', warn='#E0875F', over='#EF6A5A'),
}


def _reg_dark(name):
    if sys.platform != 'win32':
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as k:
            return winreg.QueryValueEx(k, name)[0] == 0
    except OSError:
        return False


def apps_dark():
    """Windows 'app mode' is dark."""
    return _reg_dark('AppsUseLightTheme')


def taskbar_dark():
    """Windows taskbar ('system mode') is dark."""
    return _reg_dark('SystemUsesLightTheme')


def corner(w, h, margin):
    """Top-left (x, y) for a w x h panel at the notification-area corner of the monitor under the cursor."""
    if sys.platform != 'win32':
        return None
    from ctypes import wintypes

    class MONITORINFO(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT),
                    ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD)]
    try:
        u = ctypes.windll.user32
        pt = wintypes.POINT()
        u.GetCursorPos(ctypes.byref(pt))
        u.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        u.MonitorFromPoint.restype = ctypes.c_void_p
        u.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if not u.GetMonitorInfoW(u.MonitorFromPoint(pt, 2), ctypes.byref(mi)):   # 2 = nearest monitor
            return None
    except Exception:
        return None
    m, wa = mi.rcMonitor, mi.rcWork
    x = wa.left + margin if wa.left > m.left else wa.right - w - margin    # taskbar on the left?
    y = wa.top + margin if wa.top > m.top else wa.bottom - h - margin      # taskbar on top?
    return x, y


def time_left(t, secs):
    m = max(0, int(secs // 60))
    h, m = divmod(m, 60)
    return t['c_h'].format(h=h, m=m) if h else t['c_m'].format(m=m)


class Card:
    """One open card. run() blocks on its own thread (own Tk root); close() may be called from any thread."""

    def __init__(self, t, get, on_settings=None, on_refresh=None, dark=None, lang='en', scale=None):
        self.t, self.get = t, get                 # get() -> {'sum': ..., 's': settings, 'hooks': bool}
        self.on_settings, self.on_refresh = on_settings, on_refresh
        self.dark = apps_dark() if dark is None else dark
        self.scale = scale
        self.c = PALETTE['dark' if self.dark else 'light']
        self.ui = 'Malgun Gothic' if lang == 'ko' else 'Segoe UI'
        self.closed = threading.Event()
        self.closed_at = 0.0                      # when a click outside closed it
        self._want_close = threading.Event()
        self._after_close = None
        self._hover = False
        self._had_focus = False
        self._ticks = 0

    def close(self):
        self._want_close.set()

    # ------------------------------------------------------------ window
    def run(self, shot=None):
        import tkinter as tk
        try:
            root = self.root = tk.Tk()
            root.withdraw()
            root.overrideredirect(True)
            root.attributes('-topmost', True)
            root.title('Usage Tray')
            root.configure(bg=self.c['bg'])
            self.k = self.scale or root.winfo_fpixels('1i') / 96.0
            self.cv = tk.Canvas(root, highlightthickness=0, bd=0, bg=self.c['bg'])
            self.cv.pack()
            self._btn_box = self._ref_box = None
            self.cv.bind('<Motion>', self._motion)
            self.cv.bind('<ButtonRelease-1>', self._click)
            self.draw()
            root.deiconify()
            root.lift()
            root.focus_force()
            self._foreground()
            root.bind('<Escape>', lambda _e: self._quit())
            root.after(50, self._tick)
            if shot:
                root.after(500, lambda: self._shot(shot))
            root.mainloop()
        finally:
            try:
                self.root.destroy()
            except Exception:
                pass
            # Tk objects must die on the thread that made them, or Tcl aborts the whole process.
            self.root = self.cv = root = None
            import gc
            gc.collect()
            self.closed.set()
            if self._after_close:
                self._after_close()

    def _hwnd(self):
        return ctypes.windll.user32.GetAncestor(self.root.winfo_id(), 2) if sys.platform == 'win32' else 0

    def _foreground(self):
        if sys.platform == 'win32':
            try:
                ctypes.windll.user32.SetForegroundWindow(self._hwnd())
            except Exception:
                pass

    def _quit(self, then=None):
        self._after_close = then
        try:
            self.root.quit()
        except Exception:
            pass

    def _tick(self):
        if self._want_close.is_set():
            return self._quit()
        if sys.platform == 'win32':
            u = ctypes.windll.user32
            hwnd = self._hwnd()
            fg = u.GetForegroundWindow()
            if fg == hwnd:
                self._had_focus = True
            elif fg and self._had_focus:                    # Alt+Tab, Win key, a click on another window
                self.closed_at = time.time()
                return self._quit()
            # a click anywhere outside closes it
            if (u.GetAsyncKeyState(0x01) | u.GetAsyncKeyState(0x02)) & 0x8000:
                x, y = self.root.winfo_pointerxy()
                rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
                if not (rx <= x < rx + self.root.winfo_width() and ry <= y < ry + self.root.winfo_height()):
                    self.closed_at = time.time()
                    return self._quit()
        self._ticks += 1
        if self._ticks % 100 == 0:                      # numbers move slowly; redraw every 5 s
            self.draw()
        self.root.after(50, self._tick)

    def _shot(self, path):
        from PIL import ImageGrab
        self.root.update()
        x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
        ImageGrab.grab(bbox=(x, y, x + self.root.winfo_width(), y + self.root.winfo_height()),
                       all_screens=True).save(path)
        self._quit()

    # ------------------------------------------------------------ drawing
    def font(self, px, bold=False, family=None, underline=False):
        f = (family or self.ui, -max(8, round(px * self.k)))
        style = ('bold' if bold else 'normal',) + (('underline',) if underline else ())
        return f + style

    def draw(self):
        c, t, k, cv = self.c, self.t, self.k, self.cv
        d = self.get() or {}
        s, st = d.get('sum'), d.get('s') or {}
        alert = st.get('alert_pct', 85)
        cv.delete('all')

        def K(v):
            return round(v * k)
        W, SP = K(320), K(24)            # card width, spine width
        L, R = K(20), K(320 - 24 - 20)   # content left / right
        num = 'Segoe UI Semibold'
        y = K(18)

        def text(x, y, s, size, color='ink', bold=False, anchor='nw', family=None, **kw):
            return cv.create_text(x, y, text=s, fill=c[color], font=self.font(size, bold, family), anchor=anchor, **kw)

        def rule(y, width=1, color='line'):
            cv.create_rectangle(L, y, R, y + K(width) - 1, fill=c[color], width=0)

        # head: what this is
        text(L, y, t['c_block'], 12, 'sub')
        y += K(22)

        lv = 'none'
        if not s:
            text(L, y, t['c_reading'], 15)
            y += K(30)
        elif not s['active']:
            text(L, y - K(4), t['c_idle'], 34, family=num)
            y += K(52)
            y = self._wrap(L, y, R - L, t['c_idle_sub'], 12, 'sub')
        else:
            pct = s['pct']
            lv = level(pct, alert)
            big = '–' if pct is None else str(int(pct))
            i = text(L - K(2), y - K(8), big, 50, 'ink' if lv in ('ok', 'none') else lv, family=num)
            bx = cv.bbox(i)
            if pct is not None:
                text(bx[2] + K(2), bx[3] - K(14), '%', 22, 'sub', family=num, anchor='sw')
            # used / cap on the right, aligned to the number's baseline area
            used = '%s / %s' % (_short(s['block_used']), _short(s['limit']))
            text(R, bx[3] - K(36), used, 15, bold=True, anchor='se', family='Segoe UI')
            text(R, bx[3] - K(14), t['c_cap_auto'] if s['limit_is_auto'] else t['c_cap_set'], 12, 'sub', anchor='se')
            y = bx[3] - K(4)

            # meter: flat bar, a tick where the warning sits
            bh = K(8)
            cv.create_rectangle(L, y, R, y + bh - 1, fill=c['track'], width=0)
            if pct:
                fx = L + round((R - L) * min(pct, 100) / 100)
                cv.create_rectangle(L, y, fx, y + bh - 1, fill=c[lv], width=0)
            tx = L + round((R - L) * alert / 100)
            cv.create_rectangle(tx, y - K(3), tx + max(1, K(2)) - 1, y + bh + K(3) - 1, fill=c['ink'], width=0)
            text(tx, y + bh + K(5), '%d%%' % alert, 10, 'sub', anchor='n')
            y += bh + K(26)

            # when
            left = time_left(t, s['resets_at'] - time.time())
            text(L, y, t['c_reset'].format(t=_hhmm(s['resets_at']), left=left), 13)
            y += K(22)
            if s.get('hits_limit_at') and s['hits_limit_at'] < s['resets_at']:
                text(L, y, t['hits'].format(t=_hhmm(s['hits_limit_at'])), 13, 'warn' if lv != 'over' else 'over')
                y += K(22)
            if pct is None:
                y = self._wrap(L, y, R - L, t['c_learning'], 12, 'sub')

        # today / week
        y += K(8)
        rule(y)
        y += K(12)
        half = L + (R - L) // 2
        if s:
            for x, key, v in ((L, 'c_today', s.get('today')), (half, 'c_week', s.get('week'))):
                text(x, y, t[key], 12, 'sub')
                text(x, y + K(18), _short(v), 20, bold=True, family='Segoe UI')
        y += K(50)

        # alerts
        rule(y)
        y += K(12)
        text(L, y, t['c_alerts'], 12, 'sub')
        y += K(22)
        hooked = d.get('hooks', False)
        items = ((t['c_a_limit'].format(p=alert), True, True),
                 (t['c_a_done'], st.get('notify_done'), hooked),
                 (t['c_a_ask'], st.get('notify_ask'), hooked),
                 (t['c_a_phone'], bool(st.get('ntfy_topic')), True))
        col2 = L + round((R - L) * .56)
        for n, (label, on, live) in enumerate(items):
            x = L if n % 2 == 0 else col2
            yy = y + (n // 2) * K(22)
            sz = K(9)
            col = c['ink'] if (on and live) else c['sub']
            cv.create_rectangle(x, yy + K(4), x + sz - 1, yy + K(4) + sz - 1, width=max(1, K(1.5)), outline=col,
                                fill=col if (on and live) else c['bg'])
            text(x + sz + K(7), yy - K(1), label, 12, 'ink' if (on and live) else 'sub')
        y += K(44) + K(4)
        if not hooked:
            y = self._wrap(L, y, R - L, t['c_nohooks'], 12, 'warn')
            y += K(2)

        # foot: thick ink rule, settings button (spine-edge style), refresh link, note
        y += K(6)
        rule(y, 2, 'ink')
        y += K(14)
        self._btn_y = y
        self._draw_button()
        ref = cv.create_text(R, y + K(18), text=t['refresh'], fill=c['sub'], font=self.font(12, underline=True),
                             anchor='e', tags=('ref',))
        self._ref_box = cv.bbox(ref)
        y += K(36) + K(12)
        y = self._wrap(L, y, R - L, t['about'], 11, 'sub')
        y += K(14)

        H = y
        # spine on the right edge, with the name stacked like a book spine
        cv.create_rectangle(W - SP, 0, W, H, fill=c['spine'], width=0)
        name = t['c_spine']
        if any('가' <= ch <= '힣' for ch in name):
            cv.create_text(W - SP / 2, K(16), text='\n'.join(name), fill=c['on_spine'], anchor='n',
                           font=self.font(12, family=self.ui), justify='center')
        else:
            cv.create_text(W - SP / 2, K(16), text=name, fill=c['on_spine'], angle=-90, anchor='w',
                           font=self.font(12, family=self.ui))
        # one thin ink frame, no shadow
        cv.create_rectangle(0, 0, W - 1, H - 1, outline=c['edge'], width=1)
        cv.configure(width=W, height=H)
        pos = corner(W, H, K(12))
        if pos:
            self.root.geometry('%dx%d+%d+%d' % (W, H, pos[0], pos[1]))
        else:
            self.root.geometry('%dx%d' % (W, H))

    def _wrap(self, x, y, width, s, size, color):
        i = self.cv.create_text(x, y, text=s, fill=self.c[color], font=self.font(size), anchor='nw', width=width)
        return self.cv.bbox(i)[3] + round(4 * self.k)

    def _draw_button(self):
        c, cv, k = self.c, self.cv, self.k
        cv.delete('btn')
        L, y = round(20 * k), self._btn_y
        pull = round(6 * k) if self._hover else 0
        edge = round((10 if self._hover else 4) * k)
        w, h = round(84 * k), round(36 * k)
        x0 = L - pull
        cv.create_rectangle(x0, y, x0 + w - 1, y + h - 1, fill=c['spine'], width=0, tags=('btn',))
        cv.create_rectangle(x0 + w - edge, y, x0 + w - 1, y + h - 1, fill=c['ink'] if not self.dark else c['bg'],
                            width=0, tags=('btn',))
        cv.create_text(x0 + (w - edge) / 2, y + h / 2, text=self.t['c_settings'], fill=c['on_spine'],
                       font=self.font(13, bold=True), tags=('btn',))
        self._btn_box = (L - round(6 * k), y, L + w, y + h)

    def _inside(self, box, e):
        return bool(box) and box[0] <= e.x <= box[2] and box[1] <= e.y <= box[3]

    def _motion(self, e):
        self._set_hover(self._inside(self._btn_box, e))
        on_ref = self._inside(self._ref_box, e)
        self.cv.itemconfigure('ref', fill=self.c['ink' if on_ref else 'sub'])
        self.cv.configure(cursor='hand2' if (self._hover or on_ref) else '')

    def _click(self, e):
        if self._inside(self._btn_box, e):
            if self.on_settings:           # ask for the menu while this window still holds the foreground
                self.on_settings()
            self._quit()
        elif self._inside(self._ref_box, e):
            self._refresh()

    def _set_hover(self, on):
        if on != self._hover:
            self._hover = on
            self._draw_button()

    def _refresh(self):
        if self.on_refresh:
            self.on_refresh()
        self.root.after(1500, self.draw)


def _short(n):
    from .usage import short
    return short(n)


def _hhmm(t):
    return time.strftime('%H:%M', time.localtime(t)) if t else '-'
