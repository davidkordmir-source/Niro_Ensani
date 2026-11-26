"""Native Qt styles and bundled typefaces."""

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase

ASSETS = Path(__file__).parent / "assets"
FONT_NAMES = {
    "vazir": "Vazirmatn",
    "estedad": "Estedad",
    "plex": "IBM Plex Sans Arabic",
    "cairo": "Cairo",
    "changa": "Changa",
    "messiri": "El Messiri",
    "alexandria": "Alexandria",
    "naskh": "Noto Naskh Arabic",
    "kufi": "Noto Kufi Arabic",
    "lalezar": "Lalezar",
    "markazi": "Markazi Text",
    "system": "",
}


@dataclass(frozen=True, slots=True)
class Palette:
    page: str
    surface: str
    raised: str
    line: str
    text: str
    muted: str
    accent: str
    accent_hover: str
    soft: str
    sidebar: str
    sidebar_hover: str
    sidebar_text: str
    sidebar_muted: str
    hero_start: str
    hero_end: str
    hero_secondary: str
    mint: str
    good: str
    warning: str
    is_dark: bool = False


THEMES = {
    "violet": Palette("#f6f4fa", "#ffffff", "#faf8fc", "#e9e3f1", "#292039", "#675d77", "#6a46b6", "#553594", "#eee7fa",
                      "#201633", "#3b2b55", "#f8f3ff", "#c4b6d8", "#31204b", "#5e4294", "#e4d5fa", "#c8eedf", "#187454", "#aa4129"),
    "midnight": Palette("#171521", "#222032", "#29263a", "#37334a", "#f7f1ff", "#c5b9d3", "#714bbb", "#794fc9", "#3c2b59",
                        "#100e19", "#302541", "#fbf3ff", "#c9badd", "#302145", "#59457d", "#efe2fa", "#bcead6", "#a5e4c8", "#ffb5a5", is_dark=True),
    "jade": Palette("#f0f6f3", "#ffffff", "#f7fbf8", "#dce9e1", "#203a2e", "#4e685b", "#226c54", "#1b5a45", "#e1f1e7",
                    "#122b25", "#25473b", "#f3fff8", "#bddecb", "#17392e", "#396d55", "#d6f2e0", "#c2eed3", "#1f704e", "#a44231"),
    "sand": Palette("#faf5ee", "#fffdf9", "#fcf8f2", "#eadfd0", "#3e2e2b", "#756056", "#875023", "#6f411c", "#f6e9d6",
                    "#34231f", "#53382d", "#fff7eb", "#e1cabe", "#462e28", "#7f4e37", "#f5decd", "#d9eacd", "#466a37", "#9f432a"),
    "cobalt": Palette("#eef4fd", "#ffffff", "#f4f8ff", "#d6e2f5", "#1b3052", "#4f6380", "#2458a7", "#1e4787", "#e0ebfd",
                      "#152b50", "#234270", "#f4f8ff", "#bdcee8", "#18305c", "#2f5994", "#dce8fe", "#a8ebe9", "#176951", "#a43e30"),
    "rose": Palette("#fcf2f6", "#fffdfd", "#fff7fa", "#eddbe4", "#49283a", "#765469", "#96395f", "#7e294c", "#f6e1eb",
                    "#412338", "#61324e", "#fff4f9", "#ead0de", "#4d2540", "#82466a", "#f8dcea", "#f7dba3", "#236e57", "#a32439"),
    "arctic": Palette("#eef8fb", "#fcfeff", "#f4fbfd", "#d0e8ef", "#183845", "#4d6670", "#17657a", "#12546a", "#d9eff5",
                      "#13313d", "#214b5d", "#f1feff", "#b8dce7", "#143f53", "#2d687e", "#d4eff5", "#bceae0", "#236a52", "#a13e28"),
    "copper": Palette("#fbf2ed", "#fffdfa", "#fdf6f0", "#eeded2", "#442d26", "#76574d", "#a0442b", "#813520", "#f8e5d9",
                      "#3f2821", "#624033", "#fff5ee", "#ebcdbb", "#513024", "#8a4835", "#f8dfcd", "#fbe3a6", "#396847", "#9b3925"),
    "graphite": Palette("#141922", "#1c2330", "#252e3d", "#344055", "#f2f6ff", "#b7c5d9", "#425f9f", "#4b67a6", "#2a3d62",
                        "#11151e", "#27354a", "#f5f8ff", "#b7c6da", "#202f4b", "#3e567c", "#dce7fa", "#a8dbf7", "#a1e4ca", "#ffbaaa", is_dark=True),
    "forest": Palette("#14211d", "#1b2b24", "#25382f", "#354d41", "#f0fbf4", "#bad2c3", "#26684e", "#2a7254", "#294b3c",
                      "#102019", "#254335", "#f2fff7", "#bedbd0", "#18382b", "#366553", "#dcf7e8", "#c9efb5", "#a7e4c1", "#ffc1a3", is_dark=True),
    "wine": Palette("#231820", "#30222b", "#3a2a35", "#523b4a", "#fff1fa", "#e1becf", "#9a4368", "#a04770", "#543044",
                    "#1c1220", "#472a42", "#fff3fb", "#e5c5dc", "#3f1f38", "#74405f", "#fce0f0", "#ffc7b3", "#b5eacb", "#ffbcae", is_dark=True),
    "espresso": Palette("#241b16", "#30241c", "#3b2d23", "#514032", "#faf0e7", "#dcc6b4", "#895427", "#905a2c", "#513825",
                        "#1d1511", "#423126", "#fff4e9", "#e4cec0", "#38271c", "#725339", "#f1dfcb", "#f4dea4", "#bfdfac", "#ffb6a4", is_dark=True),
}


def load_fonts() -> None:
    for file in sorted((ASSETS / "fonts").glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(file))


def interface_font(key: str, fallback: QFont) -> QFont:
    family = FONT_NAMES.get(key, FONT_NAMES["vazir"])
    font = QFont(family) if family else QFont(fallback)
    font.setPointSizeF(10.4)
    return font


def style(p: Palette) -> str:
    arrow = (ASSETS / ("chevron-dark.png" if p.is_dark else "chevron-light.png")).as_posix()
    return f"""
    QWidget {{ color: {p.text}; }}
    QMainWindow, QDialog, QWidget#stage, QWidget#scrollContent {{ background: {p.page}; }}
    QScrollArea, QStackedWidget, QLabel {{ border: none; }}
    QWidget#page {{ background: {p.page}; }}
    QFrame#sidebar {{ background: {p.sidebar}; }}
    QFrame#sidebar QLabel {{ color: {p.sidebar_text}; }}
    QFrame#sidebar QLabel#brandName {{ font-size: 26px; font-weight: 700; }}
    QFrame#sidebar QLabel#sidebarMuted {{ color: {p.sidebar_muted}; }}
    QFrame#sidebar QFrame#sourceBox {{ background: {p.sidebar_hover}; border-radius: 17px; }}
    QFrame#sidebar QPushButton#sidebarAction {{ background: {p.sidebar}; border: 1px solid {p.sidebar_muted}; color: {p.sidebar_text}; padding: 7px 12px; }}
    QFrame#sidebar QPushButton#sidebarAction:hover {{ background: {p.sidebar_hover}; }}
    QFrame#sidebar QPushButton#nav {{ text-align: left; color: {p.sidebar_muted}; background: transparent; border: none; padding: 12px 14px; font-weight: 600; border-radius: 12px; }}
    QFrame#sidebar QPushButton#nav:checked, QFrame#sidebar QPushButton#nav:hover {{ background: {p.sidebar_hover}; color: {p.sidebar_text}; }}
    QLabel#pageTitle {{ color: {p.text}; font-size: 30px; font-weight: 700; }}
    QLabel#sectionTitle {{ color: {p.text}; font-size: 17px; font-weight: 700; }}
    QLabel#muted {{ color: {p.muted}; }}
    QLabel#smallMuted {{ color: {p.muted}; font-size: 12px; }}
    QLabel#metricValue {{ color: {p.text}; font-size: 28px; font-weight: 700; }}
    QLabel#chip {{ background: {p.soft}; color: {p.text}; padding: 4px 10px; border-radius: 10px; font-size: 12px; font-weight: 600; }}
    QLabel#warning {{ color: {p.warning}; }}
    QLabel#heroTitle {{ color: #ffffff; font-size: 23px; font-weight: 700; }}
    QLabel#heroMeta {{ color: {p.hero_secondary}; }}
    QLabel#heroTag {{ color: {p.mint}; font-size: 12px; font-weight: 700; }}
    QFrame#surface {{ background: {p.surface}; border: 1px solid {p.line}; border-radius: 17px; }}
    QFrame#rule {{ background: {p.raised}; border: 1px solid {p.line}; border-radius: 10px; }}
    QFrame#divider {{ background: {p.line}; border: none; }}
    QPushButton, QToolButton {{ background: {p.surface}; color: {p.text}; border: 1px solid {p.line}; border-radius: 9px; padding: 8px 13px; }}
    QPushButton:hover, QToolButton:hover {{ background: {p.soft}; }}
    QPushButton:pressed, QToolButton:pressed {{ background: {p.raised}; }}
    QPushButton:focus, QToolButton:focus {{ border-color: {p.accent}; }}
    QPushButton:disabled, QToolButton:disabled {{ background: {p.raised}; color: {p.muted}; }}
    QPushButton#primary {{ border-color: {p.accent}; background: {p.accent}; color: white; font-weight: 700; }}
    QPushButton#primary:hover {{ border-color: {p.accent_hover}; background: {p.accent_hover}; }}
    QPushButton#primary:disabled {{ background: {p.raised}; border-color: {p.line}; color: {p.muted}; }}
    QPushButton#danger {{ color: {p.warning}; }}
    QPushButton#danger:disabled {{ color: {p.muted}; }}
    QPushButton#swatch {{ padding: 9px 12px; text-align: center; }}
    QPushButton#swatch:checked {{ border: 2px solid {p.accent}; background: {p.soft}; }}
    QLineEdit, QPlainTextEdit, QComboBox, QListWidget {{
        background: {p.surface}; color: {p.text}; border: 1px solid {p.line}; border-radius: 9px; padding: 7px 10px;
        selection-background-color: {p.soft}; selection-color: {p.text};
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QListWidget:focus {{ border-color: {p.accent}; }}
    QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled {{ background: {p.raised}; color: {p.muted}; }}
    QComboBox {{ padding-left: 26px; padding-right: 26px; }}
    QComboBox::drop-down {{ border: none; width: 25px; }}
    QComboBox::down-arrow {{ image: url("{arrow}"); width: 15px; height: 15px; }}
    QComboBox QLineEdit {{ background: transparent; border: none; padding: 0; }}
    QComboBox QAbstractItemView {{ border: 1px solid {p.line}; padding: 3px; outline: 0; }}
    QAbstractItemView {{ background: {p.surface}; color: {p.text}; selection-background-color: {p.soft}; selection-color: {p.text}; }}
    QTableView {{ background: {p.surface}; alternate-background-color: {p.raised}; border: 1px solid {p.line}; border-radius: 9px; gridline-color: {p.line}; }}
    QTableView::item {{ padding: 4px 10px; }}
    QTableView::item:selected {{ background: {p.soft}; color: {p.text}; }}
    QHeaderView {{ background: {p.raised}; }}
    QHeaderView::section {{ background: {p.raised}; color: {p.text}; padding: 7px 10px; border: none; border-bottom: 1px solid {p.line}; font-weight: 700; }}
    QTableCornerButton::section {{ background: {p.raised}; border: none; }}
    QScrollBar:vertical {{ background: {p.raised}; border: none; width: 12px; margin: 0; }}
    QScrollBar:horizontal {{ background: {p.raised}; border: none; height: 12px; margin: 0; }}
    QScrollBar::handle {{ background: {p.line}; min-width: 34px; min-height: 34px; border: 2px solid {p.raised}; border-radius: 6px; }}
    QScrollBar::handle:hover {{ background: {p.muted}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
    QToolTip {{ color: {p.text}; background: {p.surface}; border: 1px solid {p.line}; padding: 4px 8px; }}
    """
