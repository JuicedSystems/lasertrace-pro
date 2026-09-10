DARK_QSS = """
QWidget { background: #1e1f22; color: #e6e6e6; font-size: 13px; }
QMainWindow::separator { background: #2b2d31; width: 4px; }
QGroupBox { border: 1px solid #3a3d42; border-radius: 6px; margin-top: 12px; padding: 8px 6px 6px 6px; font-weight: 600; color: #b9bcc3; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QPushButton { background: #2f3238; border: 1px solid #454952; border-radius: 5px; padding: 6px 12px; }
QPushButton:hover { background: #3a3e46; }
QPushButton:pressed { background: #262930; }
QPushButton#primary { background: #ff8a1f; color: #14110c; font-weight: 700; border: none; padding: 10px 14px; font-size: 14px; }
QPushButton#primary:hover { background: #ffa04d; }
QPushButton:checked { background: #ff8a1f; color: #14110c; }
QSlider::groove:horizontal { height: 6px; background: #3a3d42; border-radius: 3px; }
QSlider::handle:horizontal { width: 16px; margin: -6px 0; background: #ff8a1f; border-radius: 8px; }
QSlider::sub-page:horizontal { background: #b0611c; border-radius: 3px; }
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit { background: #2a2c31; border: 1px solid #454952; border-radius: 4px; padding: 3px 6px; }
QListWidget { background: #24262a; border: 1px solid #3a3d42; border-radius: 4px; }
QListWidget::item { padding: 4px; }
QListWidget::item:selected { background: #3a3e46; }
QLabel#paste { border: 2px dashed #5a5e66; border-radius: 12px; color: #9aa0aa; font-size: 16px; padding: 24px; }
QLabel#paste[armed="true"] { border-color: #ff8a1f; color: #ff8a1f; }
QLabel#stat { color: #c8ccd4; font-family: Consolas, monospace; }
QLabel#warn { color: #ffb454; }
QLabel#error { color: #ff6b6b; }
QLabel#info { color: #8fb8ff; }
QToolButton { background: #2f3238; border: 1px solid #454952; border-radius: 4px; padding: 4px 8px; }
QToolButton:checked { background: #ff8a1f; color: #14110c; }
QScrollArea { border: none; }
QStatusBar { background: #17181a; color: #9aa0aa; }
QCheckBox::indicator { width: 14px; height: 14px; }
QMenuBar { background: #17181a; color: #c8ccd4; }
QMenuBar::item { padding: 4px 10px; background: transparent; }
QMenuBar::item:selected { background: #2f3238; color: #ffffff; }
QMenu { background: #24262a; border: 1px solid #3a3d42; padding: 4px; }
QMenu::item { padding: 5px 22px 5px 18px; border-radius: 4px; }
QMenu::item:selected { background: #ff8a1f; color: #14110c; }
QMenu::separator { height: 1px; background: #3a3d42; margin: 4px 8px; }
QTextBrowser { background: #24262a; border: 1px solid #3a3d42; border-radius: 4px; padding: 8px; }
QTreeWidget { background: #24262a; border: 1px solid #3a3d42; border-radius: 4px; }
QTreeWidget::item { padding: 3px 2px; }
QTreeWidget::item:selected { background: #ff8a1f; color: #14110c; }
QToolButton#help { color: #8fb8ff; font-weight: 700; border: none; background: transparent; padding: 0 4px; }
QToolButton#help:hover { color: #ff8a1f; }
"""
