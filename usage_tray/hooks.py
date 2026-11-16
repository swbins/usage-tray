"""Claude Code hooks: add/remove our two entries in ~/.claude/settings.json.

Notification (permission prompts, "waiting for input") and Stop (a turn finished) POST the
hook's JSON to the tray app on 127.0.0.1. curl ships with Windows 10+ and macOS, so the
hook needs no Python. If the tray app is not running the POST just fails quietly (-m 2).
Other hooks in the file are left alone; a backup is written next to it before each change.
"""
import json
import os
import shutil
import time

PORT = 47615
MARK = '127.0.0.1:%d/hook' % PORT
EVENTS = ('Notification', 'Stop')
SETTINGS = os.path.join(os.path.expanduser('~'), '.claude', 'settings.json')


def command(event):
    return ('curl -s -m 2 -X POST -H "Content-Type: application/json" --data-binary @- '
            'http://%s/%s' % (MARK, event.lower()))


def _load(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8') as f:
        txt = f.read()
    return json.loads(txt) if txt.strip() else {}


def _save(path, data):
    if os.path.exists(path):
        shutil.copy2(path, path + '.usage-tray-backup')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp%d' % int(time.time() * 1000)
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')
    os.replace(tmp, path)


def _ours(group):
    return any(MARK in str(h.get('command', '')) for h in (group or {}).get('hooks', []) if isinstance(h, dict))


def installed(path=SETTINGS):
    try:
        hooks = _load(path).get('hooks') or {}
    except (OSError, ValueError):
        return False
    return all(any(_ours(g) for g in hooks.get(e) or []) for e in EVENTS)


def install(path=SETTINGS):
    data = _load(path)                 # a broken settings.json raises — we never overwrite it
    hooks = data.setdefault('hooks', {})
    for e in EVENTS:
        groups = [g for g in hooks.get(e) or [] if not _ours(g)]
        groups.append({'hooks': [{'type': 'command', 'command': command(e)}]})
        hooks[e] = groups
    _save(path, data)


def uninstall(path=SETTINGS):
    data = _load(path)
    hooks = data.get('hooks') or {}
    for e in EVENTS:
        if e in hooks:
            hooks[e] = [g for g in hooks[e] or [] if not _ours(g)]
            if not hooks[e]:
                del hooks[e]
    if 'hooks' in data and not hooks:
        del data['hooks']
    _save(path, data)
