"""Entry point of the Windows exe (and of ``python -m knowledgeview --desktop``).

    KnowledgeView-Setup.exe            not installed yet -> install window
    KnowledgeView.exe                  run: server + tray, opens the browser
    KnowledgeView.exe --tray           run without opening the browser (sign-in start)
    KnowledgeView.exe --install        show the install window again (change folders/port)
    KnowledgeView.exe --uninstall      remove (Apps & Features calls this)
    ... --restarted                    started by "다시 시작": wait for the previous copy to exit first
    ... --no-install                   run a frozen build in place without installing
"""
from __future__ import annotations

import logging
import sys

from . import installer


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if "--uninstall" in argv:
            installer.uninstall()
            return 0
        if "--install" in argv or (installer.needs_install() and "--no-install" not in argv):
            installer.run_installer()
            return 0
        from .app import run

        return run(open_browser="--tray" not in argv, restarted="--restarted" in argv)
    except Exception as e:  # noqa: BLE001 - a windowed exe has nowhere else to report
        logging.getLogger("knowledgeview.desktop").exception("fatal")
        from .app import MB_ICONWARN, MB_OK, message

        message(f"KnowledgeView 오류:\n\n{e}", MB_OK | MB_ICONWARN)
        return 1
