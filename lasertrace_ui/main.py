from __future__ import annotations

import sys


ASSETS = __import__("pathlib").Path(__file__).resolve().parent / "assets"


def main() -> int:
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication
    from .app import MainWindow
    from .theme import DARK_QSS

    if sys.platform == "win32":
        # give the process its own taskbar identity so Windows shows our icon, not python's
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("JuicedSystems.LaserTracePro")
        except Exception:
            pass
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("LaserTrace Pro")
    app.setOrganizationName("Juiced Systems")
    ico = ASSETS / "icon.ico"
    if ico.exists():
        app.setWindowIcon(QIcon(str(ico)))
    app.setStyleSheet(DARK_QSS)
    win = MainWindow()
    win.show()
    if len(sys.argv) > 1:
        win.open_path(sys.argv[1])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
