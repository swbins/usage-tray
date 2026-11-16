"""Build dist/UsageTray.exe (one file, no console) with PyInstaller.

    pip install -r requirements.txt pyinstaller
    python build_exe.py
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, 'build')
DIST = os.path.join(HERE, 'dist')
ICO = os.path.join(BUILD, 'app.ico')
ENTRY = os.path.join(BUILD, 'entry.py')


def make_icon():
    sys.path.insert(0, HERE)
    from usage_tray.app import render_icon
    render_icon(42, size=256).save(ICO, format='ICO', sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def main():
    os.makedirs(BUILD, exist_ok=True)
    make_icon()
    with open(ENTRY, 'w', encoding='utf-8') as f:     # absolute import — PyInstaller runs the entry as __main__
        f.write('import sys\nfrom usage_tray.app import main\nsys.exit(main())\n')
    subprocess.check_call([
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--noconsole',
        '--name', 'UsageTray', '--icon', ICO, '--paths', HERE,
        '--hidden-import', 'pystray._win32',
        '--distpath', DIST, '--workpath', os.path.join(BUILD, 'work'), '--specpath', BUILD,
        ENTRY], cwd=HERE)
    print('built', os.path.join(DIST, 'UsageTray.exe'))


if __name__ == '__main__':
    main()
    shutil.rmtree(os.path.join(BUILD, 'work'), ignore_errors=True)
