"""Thème unique de l'appli : noir OLED, look Clash of Clans (or, vert des boutons, élixir).
Toutes les couleurs, tailles et espacements viennent d'ici."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPixmap, QPainter
from PyQt6.QtSvg import QSvgRenderer

ICI = Path(__file__).resolve().parent
DOSSIER_UI = ICI / "ui"

# --- couleurs -------------------------------------------------------------
NOIR = "#000000"          # fond OLED
SURFACE = "#0E0E13"       # cartes
SURFACE_2 = "#17171F"     # champs, boutons
SURFACE_3 = "#22222C"     # survol
BORD = "#2C2C38"
TEXTE = "#F4F4F7"
TEXTE_2 = "#B4B4C4"       # texte secondaire (bien lisible sur noir)
TEXTE_3 = "#7C7C8E"       # indices, désactivé
OR = "#FFC531"            # or CoC : titres, accent
OR_FONCE = "#B98A12"
VERT = "#6CC24A"          # bouton vert CoC
VERT_FONCE = "#3F8A2B"
VERT_TEXTE = "#0A1A06"    # texte sur bouton vert
ROUGE = "#FF5A5A"
ROUGE_FONCE = "#A8302F"
ORANGE = "#FFA23A"        # nuit
BLEU = "#4DB3FF"

CATEGORIE = {
    "Ouvriers": OR,
    "Labo": "#D46BFF",        # élixir
    "Animalerie": "#3DDC97",
    "Forge": "#FF8A4C",
    "Assistants": "#FF7EB6",
    "Base ouvriers": BLEU,
}
ICONE_CATEGORIE = {
    "Ouvriers": "marteau",
    "Labo": "fiole",
    "Animalerie": "patte",
    "Forge": "enclume",
    "Assistants": "assistant",
    "Base ouvriers": "bateau",
}
NOM_LIBRE = {   # ce qui se libère quand la catégorie a fini
    "Ouvriers": "Prochain ouvrier libre",
    "Labo": "Laboratoire libre",
    "Animalerie": "Animalerie libre",
    "Forge": "Forge libre",
    "Assistants": "Assistants dispo",
    "Base ouvriers": "Base des ouvriers",
}
NOM_CATEGORIE = {
    "Ouvriers": "Ouvriers",
    "Labo": "Laboratoire",
    "Animalerie": "Animalerie",
    "Forge": "Forge",
    "Assistants": "Assistants",
    "Base ouvriers": "Base des ouvriers",
}

# --- espacements (base 4) et formes --------------------------------------------
E1, E2, E3, E4, E6, E8 = 4, 8, 12, 16, 24, 32
RAYON = 10
RAYON_CARTE = 14

# --- typo ---------------------------------------------------------------------
TITRE = "Lilita One"      # proche de la police de Clash of Clans
CORPS = "Nunito"
T_PAGE, T_SECTION, T_CORPS, T_PETIT = 24, 17, 14, 12


def charger_polices():
    for f in ("LilitaOne-Regular.ttf", "Nunito.ttf"):
        QFontDatabase.addApplicationFont(str(ICI / "polices" / f))


def police(taille=T_CORPS, titre=False, gras=False) -> QFont:
    f = QFont(TITRE if titre else CORPS)
    f.setPixelSize(taille)
    if not titre:
        f.setWeight(QFont.Weight.Bold if gras else QFont.Weight.Medium)
    return f


def qcolor(hexa: str, alpha: int | None = None) -> QColor:
    c = QColor(hexa)
    if alpha is not None:
        c.setAlpha(alpha)
    return c


# --- icônes (traits simples, 24x24) -------------------------------------------
_SVG = {
    "accueil": '<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/><path d="M10 20v-6h4v6"/>',
    "conseils": '<path d="M9 18h6"/><path d="M10 21h4"/><path d="M12 3a6 6 0 0 0-4 10.5c.8.8 1 1.5 1 2.5h6c0-1 .2-1.7 1-2.5A6 6 0 0 0 12 3z"/>',
    "outils": '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.4-.6-.6-2.4z"/>',
    "reglages": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>',
    "maj": '<path d="M20 12a8 8 0 1 1-2.3-5.7"/><path d="M20 4v5h-5"/>',
    "envoyer": '<path d="M4 12l16-8-6 16-3-7z"/>',
    "alarme": '<circle cx="12" cy="13" r="7"/><path d="M12 9v4l3 2"/><path d="M5 3L2 6M19 3l3 3"/>',
    "agenda": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    "lune": '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
    "soleil": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M5 19l1.5-1.5M17.5 6.5L19 5"/>',
    "tel": '<rect x="7" y="2" width="10" height="20" rx="2"/><path d="M11 18h2"/>',
    "ok": '<path d="M4 12l5 5L20 6"/>',
    "attention": '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18v.5"/>',
    "camera": '<path d="M4 7h4l2-3h4l2 3h4v13H4z"/><circle cx="12" cy="13" r="4"/>',
    "cible": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
    "image": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-9 9"/>',
    "presse": '<rect x="6" y="4" width="12" height="17" rx="2"/><path d="M9 4V2h6v2"/>',
    "barre": '<path d="M5 12h14"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "poubelle": '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    "robot": '<rect x="5" y="8" width="14" height="11" rx="3"/><path d="M12 4v4M9 13h.01M15 13h.01M9 16h6"/>',
    "coche": '<path d="M5 12l4 4 10-10"/>',
    "haut": '<path d="M6 15l6-6 6 6"/>',
    "marteau": '<path d="M13 4l7 7-3 3-7-7z"/><path d="M11.5 8.5L3 17l3 3 8.5-8.5"/>',
    "fiole": '<path d="M9 3h6"/><path d="M10 3v6l-5.5 9.5A2 2 0 0 0 6.2 21h11.6a2 2 0 0 0 1.7-2.5L14 9V3"/><path d="M7.5 15h9"/>',
    "patte": '<circle cx="6.5" cy="10" r="2"/><circle cx="10" cy="5.5" r="2"/><circle cx="14" cy="5.5" r="2"/><circle cx="17.5" cy="10" r="2"/><path d="M8 17.5c0-3 2-5.5 4-5.5s4 2.5 4 5.5c0 1.8-1.8 3-4 3s-4-1.2-4-3z"/>',
    "enclume": '<path d="M3 7h13c0 3 2 4.5 5 4.5V14H9c-3 0-6-2.5-6-7z"/><path d="M10 14l-2 6h9l-2-6"/>',
    "assistant": '<circle cx="12" cy="9" r="3.5"/><path d="M5 21c0-4 3-6.5 7-6.5s7 2.5 7 6.5"/><path d="M7.5 6.5L12 2.5l4.5 4"/>',
    "bateau": '<path d="M3 15h18l-3 5H6z"/><path d="M12 3v12"/><path d="M12 4l6 9h-6"/>',
    "bouclier": '<path d="M12 2l8 3v6c0 5-3.4 9-8 11-4.6-2-8-6-8-11V5z"/><circle cx="12" cy="11.5" r="4"/><path d="M12 9.5v2l1.5 1.2"/>',
    "sablier": '<path d="M6 3h12M6 21h12"/><path d="M7 3c0 5 10 5 10 9s-10 4-10 9"/><path d="M17 3c0 5-10 5-10 9s10 4 10 9"/>',
    "bas": '<path d="M6 9l6 6 6-6"/>',
}


def svg(nom: str, couleur: str = TEXTE, epaisseur: float = 2.0) -> bytes:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{couleur}" '
            f'stroke-width="{epaisseur}" stroke-linecap="round" stroke-linejoin="round">{_SVG[nom]}</svg>').encode()


def icone(nom: str, couleur: str = TEXTE, taille: int = 20) -> QIcon:
    return QIcon(pixmap(nom, couleur, taille))


def pixmap(nom: str, couleur: str = TEXTE, taille: int = 20) -> QPixmap:
    r = QSvgRenderer(QByteArray(svg(nom, couleur)))
    pm = QPixmap(taille * 2, taille * 2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    r.render(p)
    p.end()
    pm.setDevicePixelRatio(2)
    return pm


def icone_appli() -> QIcon:
    """Icône de l'appli : bouclier doré sur fond noir arrondi (dessinée, pas tirée du jeu)."""
    DOSSIER_UI.mkdir(exist_ok=True)
    f = DOSSIER_UI / "icone.png"
    if not f.exists():
        from PyQt6.QtGui import QLinearGradient, QBrush, QPainterPath
        from PyQt6.QtCore import QRectF
        pm = QPixmap(256, 256)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        chemin = QPainterPath(); chemin.addRoundedRect(QRectF(8, 8, 240, 240), 56, 56)
        g = QLinearGradient(0, 0, 0, 256); g.setColorAt(0, QColor("#1A1A22")); g.setColorAt(1, QColor("#000000"))
        p.fillPath(chemin, QBrush(g))
        p.setPen(QColor(OR_FONCE)); p.drawPath(chemin)
        r = QSvgRenderer(QByteArray(svg("bouclier", OR, 1.6)))
        r.render(p, QRectF(40, 36, 176, 176))
        p.end()
        pm.save(str(f))
    return QIcon(str(f))


def _fichier_svg(nom: str, couleur: str) -> str:
    DOSSIER_UI.mkdir(exist_ok=True)
    f = DOSSIER_UI / f"{nom}_{couleur.strip('#')}.svg"
    if not f.exists():
        f.write_bytes(svg(nom, couleur, 3))
    return str(f)


# --- feuille de style ---------------------------------------------------------
def feuille() -> str:
    coche = _fichier_svg("coche", VERT_TEXTE)
    haut = _fichier_svg("haut", TEXTE_2)
    bas = _fichier_svg("bas", TEXTE_2)
    return f"""
* {{ font-family: "{CORPS}"; font-size: {T_CORPS}px; color: {TEXTE}; }}
QMainWindow, QWidget#racine, QStackedWidget, QWidget#page, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {NOIR}; }}
QScrollArea#transparent, QWidget#transparent {{ background: transparent; }}
QToolTip {{ background: {SURFACE_2}; color: {TEXTE}; border: 1px solid {BORD}; padding: {E2}px; border-radius: 6px; }}

/* barre de navigation */
QWidget#nav {{ background: {NOIR}; border-right: 1px solid {BORD}; }}
QPushButton#navbtn {{ background: transparent; border: none; border-radius: {RAYON}px; text-align: left;
    padding: {E3}px {E4}px; font-family: "{TITRE}"; font-size: 16px; color: {TEXTE_2}; }}
QPushButton#navbtn:hover {{ background: {SURFACE_2}; color: {TEXTE}; }}
QPushButton#navbtn:checked {{ background: {SURFACE_2}; color: {OR}; border-left: 3px solid {OR}; }}
QLabel#logo {{ font-family: "{TITRE}"; font-size: 22px; color: {OR}; padding: {E2}px {E4}px; }}
QLabel#sous_logo {{ color: {TEXTE_3}; font-size: {T_PETIT}px; padding: 0 {E4}px; }}

/* textes */
QLabel#titre_page {{ font-family: "{TITRE}"; font-size: {T_PAGE}px; color: {TEXTE}; }}
QLabel#titre_section {{ font-family: "{TITRE}"; font-size: {T_SECTION}px; color: {OR}; }}
QLabel#aide {{ color: {TEXTE_2}; }}
QLabel#petit {{ color: {TEXTE_3}; font-size: {T_PETIT}px; }}
QLabel#heure {{ font-family: "{TITRE}"; font-size: 22px; color: {TEXTE}; background: transparent; border: none; }}
QLabel#heure_eteinte {{ font-family: "{TITRE}"; font-size: 22px; color: {TEXTE_3}; background: transparent; border: none; }}
QLabel#nom_fin {{ font-size: 15px; font-weight: 800; color: {TEXTE}; background: transparent; border: none; }}
QLabel#nom_eteint {{ font-size: 15px; font-weight: 700; color: {TEXTE_3}; background: transparent; border: none; }}
QLabel#jour {{ font-family: "{TITRE}"; font-size: 16px; color: {TEXTE_2}; padding-top: {E2}px; }}

/* cartes */
QFrame#carte {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #13131A, stop:1 #0A0A0E);
    border: 1px solid {BORD}; border-radius: {RAYON_CARTE}px; }}
QFrame#tuile {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #16161E, stop:1 #0B0B10);
    border: 1px solid {BORD}; border-radius: {RAYON_CARTE}px; }}
QFrame#tuile_vedette {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #2A2208, stop:1 #0E0B04);
    border: 1px solid {OR_FONCE}; border-radius: {RAYON_CARTE}px; }}
QFrame#carte_ok {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0F2A0A, stop:1 #0A0A0E); border: 1px solid {VERT_FONCE}; border-radius: {RAYON_CARTE}px; }}
QFrame#carte_alerte {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2A2208, stop:1 #0A0A0E); border: 1px solid {OR_FONCE}; border-radius: {RAYON_CARTE}px; }}
QFrame#fin {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #15151C, stop:1 #0D0D12);
    border: 1px solid {BORD}; border-radius: {RAYON}px; }}
QFrame#fin:hover {{ border-color: {TEXTE_3}; background: #17171F; }}
QFrame#fin_eteinte {{ background: {NOIR}; border: 1px dashed {BORD}; border-radius: {RAYON}px; }}

/* boutons */
QPushButton {{ background: {SURFACE_2}; border: 1px solid {BORD}; border-radius: {RAYON}px;
    padding: {E2}px {E4}px; font-weight: 700; min-height: 22px; }}
QPushButton:hover {{ background: {SURFACE_3}; border-color: {TEXTE_3}; }}
QPushButton:pressed {{ background: {BORD}; }}
QPushButton:disabled {{ color: {TEXTE_3}; background: {SURFACE}; }}
QPushButton:focus {{ border: 2px solid {OR}; }}
QPushButton#primaire {{ background: {VERT}; color: {VERT_TEXTE}; border: 1px solid {VERT_FONCE};
    border-bottom: 4px solid {VERT_FONCE}; font-family: "{TITRE}"; font-size: 17px; padding: {E3}px {E6}px; }}
QPushButton#primaire:hover {{ background: #7DD45A; }}
QPushButton#primaire:pressed {{ border-bottom: 1px solid {VERT_FONCE}; padding-top: {E3 + 3}px; }}
QPushButton#primaire:disabled {{ background: {SURFACE_2}; color: {TEXTE_3}; border-color: {BORD}; }}
QPushButton#primaire:focus {{ border: 2px solid {OR}; border-bottom: 4px solid {OR_FONCE}; }}
QPushButton#or {{ background: {OR}; color: #1E1400; border: 1px solid {OR_FONCE}; border-bottom: 4px solid {OR_FONCE};
    font-family: "{TITRE}"; font-size: 15px; }}
QPushButton#or:hover {{ background: #FFD35C; }}
QPushButton#danger {{ color: {ROUGE}; }}
QPushButton#puce {{ border-radius: 16px; padding: {E1 + 2}px {E3}px; color: {TEXTE_3}; background: {NOIR}; }}
QPushButton#puce:checked {{ color: {TEXTE}; background: {SURFACE_2}; }}
QPushButton#segment {{ border-radius: 0; padding: {E2}px {E4}px; }}
QPushButton#segment:checked {{ background: {OR}; color: #1E1400; border-color: {OR_FONCE}; }}

/* champs */
QLineEdit, QSpinBox, QDoubleSpinBox, QTimeEdit, QComboBox {{ background: {SURFACE_2}; border: 1px solid {BORD};
    border-radius: 8px; padding: {E1 + 2}px {E2}px; min-height: 22px; selection-background-color: {OR}; selection-color: #000; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTimeEdit:focus, QComboBox:focus {{ border: 2px solid {OR}; }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {BORD}; selection-background-color: {SURFACE_3};
    selection-color: {OR}; outline: none; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url({bas}); width: 12px; height: 12px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ subcontrol-origin: border; width: 22px;
    background: {SURFACE_3}; border: none; }}
QAbstractSpinBox::up-button {{ subcontrol-position: top right; border-top-right-radius: 7px; }}
QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; border-bottom-right-radius: 7px; }}
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{ background: {BORD}; }}
QAbstractSpinBox::up-arrow {{ image: url({haut}); width: 10px; height: 10px; }}
QAbstractSpinBox::down-arrow {{ image: url({bas}); width: 10px; height: 10px; }}
QCheckBox {{ spacing: {E2}px; }}
QCheckBox::indicator {{ width: 20px; height: 20px; border-radius: 6px; border: 2px solid {TEXTE_3}; background: {NOIR}; }}
QCheckBox::indicator:checked {{ background: {VERT}; border-color: {VERT}; image: url({coche}); }}
QCheckBox::indicator:hover {{ border-color: {OR}; }}
QCheckBox#interrupteur {{ font-family: "{TITRE}"; font-size: 15px; }}

/* tableaux (page Outils) */
QTableWidget {{ background: {NOIR}; alternate-background-color: {SURFACE}; gridline-color: {SURFACE_3};
    border: 1px solid {BORD}; border-radius: {RAYON}px; selection-background-color: {SURFACE_3}; selection-color: {TEXTE}; }}
QHeaderView::section {{ background: {SURFACE_2}; color: {TEXTE_2}; border: none; border-right: 1px solid {BORD};
    padding: {E1 + 2}px {E2}px; font-weight: 800; }}
QTableCornerButton::section {{ background: {SURFACE_2}; border: none; }}
QHeaderView {{ background: {SURFACE}; border: none; }}
QHeaderView::section:vertical {{ background: {SURFACE}; color: {TEXTE_3}; border: none; border-bottom: 1px solid {SURFACE_3}; }}

/* onglets */
QTabWidget::pane {{ border: 1px solid {BORD}; border-radius: {RAYON}px; top: -1px; background: {NOIR}; }}
QTabBar::tab {{ background: {SURFACE}; color: {TEXTE_2}; border: 1px solid {BORD}; border-bottom: none;
    padding: {E2}px {E4}px; margin-right: 2px; border-top-left-radius: 8px; border-top-right-radius: 8px; font-weight: 700; }}
QTabBar::tab:selected {{ background: {SURFACE_2}; color: {OR}; }}

QGroupBox {{ font-family: "{TITRE}"; font-size: 16px; background: {SURFACE}; border: 1px solid {BORD}; border-radius: {RAYON_CARTE}px; margin-top: {E6}px;
    padding: {E4}px {E3}px {E3}px {E3}px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: {E3}px; top: 0; color: {OR}; font-family: "{TITRE}"; font-size: 15px; }}

QPlainTextEdit {{ background: {NOIR}; border: 1px solid {BORD}; border-radius: {RAYON}px; color: {TEXTE_2};
    font-family: monospace; font-size: 12px; }}
QProgressBar {{ background: {SURFACE_2}; border: 1px solid {BORD}; border-radius: 8px; text-align: center; color: {TEXTE}; }}
QProgressBar::chunk {{ background: {VERT}; border-radius: 7px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SURFACE_3}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {TEXTE_3}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {SURFACE_3}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QSplitter::handle {{ background: {NOIR}; }}
QStatusBar {{ background: {NOIR}; color: {TEXTE_3}; border-top: 1px solid {BORD}; }}
QMessageBox, QDialog {{ background: {SURFACE}; }}
QMenu {{ background: {SURFACE_2}; border: 1px solid {BORD}; }}
"""
