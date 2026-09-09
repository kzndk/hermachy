#!/usr/bin/env python3
"""Install the self-contained Hermachy app and an Omarchy launcher, per user."""
from pathlib import Path
import json
import os
import shlex
import shutil
import subprocess
import tempfile

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
HOME_DIR = Path.home()
DATA = Path(os.environ.get('XDG_DATA_HOME', HOME_DIR / '.local/share'))
TARGET = DATA / 'hermachy/app'
RUNTIME = APP / 'node_modules/electron/dist'


def desktop_quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'


def main():
    if not (RUNTIME / 'electron').is_file():
        raise SystemExit('Run npm ci and npm exec install-electron in desktop/ first.')
    if not (HOME_DIR / '.local/bin/hermachy').is_file() and not shutil.which('hermachy'):
        raise SystemExit('Install and configure the Hermachy CLI first; see ../README.md.')
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.app-stage-', dir=TARGET.parent))
    try:
        shell = stage / 'desktop'
        shell.mkdir()
        for name in ('main.cjs', 'preload.cjs', 'transport.cjs', 'attachments.cjs', 'theme.cjs', 'changes.cjs', 'package.json', 'README.md'):
            shutil.copy2(APP / name, shell / name)
        for name in ('renderer', 'assets'):
            shutil.copytree(APP / name, shell / name)
        dependencies = json.loads((APP / 'package.json').read_text())['dependencies']
        for name in dependencies:
            shutil.copytree(APP / 'node_modules' / name, shell / 'node_modules' / name)
        shutil.copytree(REPO / 'src/hermachy', stage / 'src/hermachy', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(RUNTIME, stage / 'runtime')
        shutil.copy2(REPO / 'LICENSE', stage / 'LICENSE')
        previous = TARGET.with_name('app.previous')
        if previous.exists():
            shutil.rmtree(previous)
        if TARGET.exists():
            TARGET.rename(previous)
        try:
            stage.rename(TARGET)
        except OSError:
            if previous.exists():
                previous.rename(TARGET)
            raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    launcher = HOME_DIR / '.local/bin/hermachy-app'
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text('#!/usr/bin/env bash\nexec ' + shlex.quote(str(TARGET / 'runtime/electron'))
                        + ' ' + shlex.quote(str(TARGET / 'desktop')) + ' --class=Hermachy --disable-gpu "$@"\n')
    launcher.chmod(0o755)
    icon_dir = DATA / 'icons/hicolor/256x256/apps'
    icon_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(APP / 'assets/hermachy.png', icon_dir / 'hermachy.png')
    applications = DATA / 'applications'
    applications.mkdir(parents=True, exist_ok=True)
    (applications / 'hermachy.desktop').write_text(
        '[Desktop Entry]\nVersion=1.0\nType=Application\nName=Hermachy\n'
        'Comment=Your Hermes agent, with a little company\n'
        f'Exec={desktop_quote(launcher)}\nIcon=hermachy\nTerminal=false\n'
        'Categories=Development;\nKeywords=AI;Hermes;Omarchy;Chat;Assistant;\n'
        'StartupNotify=true\nStartupWMClass=Hermachy\n')
    if shutil.which('update-desktop-database'):
        subprocess.run(['update-desktop-database', str(applications)], check=True)
    print(f'Installed {TARGET}')
    print('Open Hermachy in the Omarchy launcher, or run hermachy-app.')


if __name__ == '__main__':
    main()
