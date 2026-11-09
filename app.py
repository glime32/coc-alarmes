#!/usr/bin/env python3
"""CoC Alarmes — améliorations Clash of Clans (export du jeu ou lecture d'écran) → alarmes Horloge Samsung + Google Agenda."""
from __future__ import annotations

import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from PIL import Image
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QRect, QTime, QSize
from PyQt6.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QFont
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QComboBox, QSplitter, QPlainTextEdit, QHeaderView,
    QFormLayout, QSpinBox, QCheckBox, QMessageBox, QGroupBox,
    QFileDialog, QDoubleSpinBox, QAbstractItemView, QSizePolicy, QTabWidget, QTimeEdit,
    QProgressBar, QLineEdit, QFrame, QScrollArea, QStackedWidget, QGridLayout, QGraphicsOpacityEffect,
    QSystemTrayIcon, QMenu,
)
from PyQt6.QtCore import QPropertyAnimation, QEasingCurve

import theme
from widgets import TexteCoC, BoutonCoC, Interrupteur, PastilleIcone

import core
from core import (Adb, AdbError, Amelioration, Boost, BOOST_TYPES, CATEGORIES, parse_duree,
                  fmt_duree, prevoir, analyser, apprendre_icone, load_config,
                  save_json, load_json, CONFIG_FILE, STATE_FILE, Suivi, conseiller, Eveil, repartir)
import agenda
import export
from auto_export import ExportAuto
from chrono import Chronologie, COULEURS
from core import VILLAGE_PRINCIPAL
from noms import Noms
from presse_papier import PontPressePapier

VERT = theme.qcolor(theme.VERT)
GRIS = theme.qcolor(theme.TEXTE_3)
BLEU = theme.qcolor(theme.BLEU)
ROUGE = theme.qcolor(theme.ROUGE)
ORANGE = theme.qcolor(theme.ORANGE)
JAUNE = theme.qcolor(theme.OR)


def pil_to_qimage(img: Image.Image) -> QImage:
    img = img.convert("RGB")
    data = img.tobytes("raw", "RGB")
    return QImage(data, img.width, img.height, img.width * 3, QImage.Format.Format_RGB888).copy()


def hm(epoch: float) -> str:
    d = datetime.fromtimestamp(epoch)
    n = datetime.now().date()
    ecart = (d.date() - n).days
    jour = "" if ecart == 0 else ("demain " if ecart == 1 else d.strftime("%a %d ").lower())
    return jour + d.strftime("%H:%M")


def item(txt, editable=False, couleur=None):
    it = QTableWidgetItem(txt)
    if not editable:
        it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    if couleur is not None:
        it.setForeground(couleur)
    return it


# --------------------------------------------------------------------------
class Worker(QThread):
    fini = pyqtSignal(object)
    erreur = pyqtSignal(str)

    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args

    def run(self):
        try:
            self.fini.emit(self.fn(*self.args))
        except AdbError as e:
            self.erreur.emit(f"ADB : {e}")
        except Exception:
            self.erreur.emit(traceback.format_exc())


class SuiviWorker(QThread):
    """Scan + relecture en boucle des cases jusqu'à voir changer chaque minute."""
    scan = pyqtSignal(object)
    progres = pyqtSignal(str, int, int)
    fini = pyqtSignal(object)
    erreur = pyqtSignal(str)

    def __init__(self, adb: Adb, cfg: dict, cat, max_s=80):
        super().__init__()
        self.adb, self.cfg, self.cat, self.max_s = adb, cfg, cat, max_s
        self.stop = False

    def run(self):
        try:
            self.adb.synchroniser()
            img, t = self.adb.screencap()
            res = analyser(img, t, self.cfg, self.cat)
            self.scan.emit(res)
            suivi = Suivi(res.ameliorations, res.boosts)
            if not suivi.ams:
                self.fini.emit(suivi)
                return
            debut = time.time()
            while not self.stop and time.time() - debut < self.max_s:
                tour = time.time()
                img, t = self.adb.screencap()
                for e in suivi.traiter(img, t):
                    self.progres.emit(e, 0, 0)
                n = len(suivi.ams)
                ok = n - len(suivi.pas_fini())
                self.progres.emit("", ok, n)
                if suivi.fini(t):
                    break
                # 4 lectures par seconde suffisent (±0,13 s) : la vidéo irait plus vite, pour rien
                time.sleep(max(0.0, 0.25 - (time.time() - tour)))
            suivi.appliquer()
            self.fini.emit(suivi)
        except AdbError as e:
            self.erreur.emit(f"ADB : {e}")
        except Exception:
            self.erreur.emit(traceback.format_exc())


# --------------------------------------------------------------------------
class VueEcran(QLabel):
    def __init__(self):
        super().__init__("Pas encore de capture.\n\nOuvre le menu des ouvriers dans Clash of Clans\n"
                         "puis clique « Scanner » ou « Scan précis ».")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(320, 160)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.img: QImage | None = None
        self.boites = []

    def montrer(self, img: Image.Image, boites):
        self.img = pil_to_qimage(img)
        self.boites = boites
        self.update()

    def paintEvent(self, ev):
        if self.img is None:
            return super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        s = min(w / self.img.width(), h / self.img.height())
        dw, dh = int(self.img.width() * s), int(self.img.height() * s)
        ox, oy = (w - dw) // 2, (h - dh) // 2
        p.drawImage(QRect(ox, oy, dw, dh), self.img)
        f = QFont()
        f.setBold(True)
        p.setFont(f)
        for (x1, y1, x2, y2), col, txt in self.boites:
            p.setPen(QPen(col, 2))
            r = QRect(ox + int(x1 * s) - 2, oy + int(y1 * s) - 2, int((x2 - x1) * s) + 4, int((y2 - y1) * s) + 4)
            p.drawRect(r)
            if txt:
                lw = 9 * len(txt) + 6
                p.fillRect(r.x() - lw, r.y(), lw, 16, col)
                p.setPen(QColor("black"))
                p.drawText(r.x() - lw + 3, r.y() + 13, txt)
        p.end()


# --------------------------------------------------------------------------
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def jour_lisible(epoch: float) -> str:
    d = datetime.fromtimestamp(epoch)
    ecart = (d.date() - datetime.now().date()).days
    if ecart == 0:
        return "Aujourd'hui"
    if ecart == 1:
        return "Demain"
    return f"{JOURS[d.weekday()].capitalize()} {d.day} {MOIS[d.month - 1]}"


def dans_lisible(sec: float) -> str:
    if sec <= 0:
        return "fini"
    j, r = divmod(int(sec), 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    if j:
        return f"dans {j} j {h} h"
    if h:
        return f"dans {h} h {m:02d}"
    return f"dans {max(1, m)} min"


def titre(txt: str):
    return TexteCoC(txt, 30, theme.TEXTE, 3.0)


def sous_titre(txt: str):
    return TexteCoC(txt, 19, theme.OR, 2.3)


def legende(ic: str, couleur: str, txt: str) -> QWidget:
    w = QWidget(); l = QHBoxLayout(w); l.setContentsMargins(theme.E2, 0, 0, 0); l.setSpacing(theme.E1)
    i = QLabel(); i.setPixmap(theme.pixmap(ic, couleur, 16)); l.addWidget(i)
    t = QLabel(txt); t.setObjectName("petit"); l.addWidget(t)
    return w


def badge(ic: str | None, txt: str, couleur: str, conseil: str = "") -> QFrame:
    f = QFrame()
    c = QColor(couleur)
    f.setStyleSheet(f"QFrame {{ background: rgba({c.red()},{c.green()},{c.blue()},38); border: 1px solid {couleur};"
                    f" border-radius: 11px; }} QLabel {{ background: transparent; border: none; color: {couleur};"
                    f" font-weight: 800; font-size: {theme.T_PETIT}px; }}")
    l = QHBoxLayout(f); l.setContentsMargins(theme.E2, 2, theme.E2 + 2, 2); l.setSpacing(theme.E1)
    if ic:
        i = QLabel(); i.setPixmap(theme.pixmap(ic, couleur, 14)); l.addWidget(i)
    l.addWidget(QLabel(txt))
    if conseil:
        f.setToolTip(conseil)
    return f


class FormReglages(QWidget):
    """Page Réglages : sections claires, un seul bouton Enregistrer."""

    def __init__(self, cfg, lignes_sommeil, ligne_tel, quand_enregistre, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        self.cfg = dict(cfg)
        self.quand_enregistre = quand_enregistre
        self.app = QSpinBox(); self.app.setRange(1, 8); self.app.setValue(int(cfg["niveau_apprenti"]))
        self.ass = QSpinBox(); self.ass.setRange(1, 12); self.ass.setValue(int(cfg["niveau_assistant"]))
        self.cumul = QCheckBox("La potion d'ouvrier et le Builder Bite se cumulent (x11)")
        self.cumul.setChecked(bool(cfg["potion_snack_cumul"]))
        self.cumul.setToolTip("D'après le wiki, depuis la mise à jour du 26 février : non (ça reste x10).")
        self.fen = QSpinBox(); self.fen.setRange(1, 24); self.fen.setValue(int(cfg["fenetre_heures"])); self.fen.setSuffix(" h")
        self.av = QSpinBox(); self.av.setRange(0, 60); self.av.setValue(int(cfg["avance_minutes"])); self.av.setSuffix(" min avant la fin")
        self.rel = QCheckBox("Revenir sur Clash of Clans après avoir mis les alarmes"); self.rel.setChecked(bool(cfg["relancer_coc"]))
        self.seuil = QDoubleSpinBox(); self.seuil.setRange(0.1, 1.5); self.seuil.setSingleStep(0.05)
        self.seuil.setValue(float(cfg["seuil_icone_hist"]))
        self.seuil.setToolTip("Plus haut = reconnaît les icônes de boost plus facilement (mais risque d'erreur).")
        self.jour = QComboBox()
        for k, t in (("alarme", "Alarme sur le téléphone"), ("alarme+event", "Alarme + rappel dans l'agenda"),
                     ("event", "Rappel dans l'agenda seulement")):
            self.jour.addItem(t, k)
        self.jour.setCurrentIndex(max(0, self.jour.findData(cfg["mode_jour"])))
        self.nuit = QComboBox()
        for k, t in (("event", "Rappel dans l'agenda seulement (pas d'alarme)"), ("alarme+event", "Alarme + rappel dans l'agenda"),
                     ("alarme", "Alarme sur le téléphone"), ("rien", "Rien du tout")):
            self.nuit.addItem(t, k)
        self.nuit.setCurrentIndex(max(0, self.nuit.findData(cfg["mode_nuit"])))
        self.agenda = QComboBox()
        self.agenda.addItem("Agenda du téléphone (rien à configurer)", "telephone")
        self.agenda.addItem("Google Agenda par internet (connexion une fois)", "google")
        self.agenda.setCurrentIndex(max(0, self.agenda.findData(cfg["agenda"])))
        self.compte = QLineEdit(cfg["agenda_compte"])
        self.hor = QSpinBox(); self.hor.setRange(24, 24 * 30); self.hor.setSingleStep(24); self.hor.setSuffix(" h")
        self.hor.setValue(int(cfg["horizon_event_h"]))
        self.b_google = QPushButton("Connecter Google Agenda…")
        self.b_google.clicked.connect(self.connecter_google)
        self.lbl_google = QLabel("connecté" if agenda.AgendaGoogle.pret() else "pas connecté")
        self.lbl_google.setObjectName("aide")
        self.rappel = QSpinBox(); self.rappel.setRange(0, 240); self.rappel.setSuffix(" min avant (et une autre à l'heure)")
        self.rappel.setValue(int(cfg["rappel_avant_min"]))
        self.inter = QSpinBox(); self.inter.setRange(0, 24 * 60); self.inter.setSingleStep(30)
        self.inter.setSpecialValueText("jamais (seulement quand tu copies l'export)"); self.inter.setSuffix(" min")
        self.inter.setValue(int(cfg["auto_export_min"]))
        self.potions = QCheckBox("Lire aussi les potions actives (ouvre le menu des ouvriers, +3 s)")
        self.potions.setChecked(bool(cfg.get("lire_potions", True)))
        self.auto_env = QCheckBox("Envoyer sans demander à chaque nouvel export")
        self.auto_env.setChecked(bool(cfg["auto_envoi"]))

        racine = QVBoxLayout(self)
        racine.setContentsMargins(0, 0, 0, 0)
        racine.setSpacing(theme.E4)
        racine.addWidget(titre("Réglages"))
        zone = QScrollArea(); zone.setWidgetResizable(True); zone.setFrameShape(QFrame.Shape.NoFrame)
        interieur = QWidget(); interieur.setObjectName("page")
        grille = QGridLayout(interieur)
        grille.setContentsMargins(0, 0, theme.E2, 0)
        grille.setHorizontalSpacing(theme.E4); grille.setVerticalSpacing(theme.E2)

        def section(nom, aide_txt, lignes):
            g = QGroupBox(nom)
            f = QFormLayout(g)
            f.setHorizontalSpacing(theme.E4); f.setVerticalSpacing(theme.E3)
            f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            if aide_txt:
                l = QLabel(aide_txt); l.setObjectName("aide"); l.setWordWrap(True)
                f.addRow(l)
            for lab, w in lignes:
                if lab is None:
                    f.addRow(w)
                else:
                    f.addRow(lab, w)
            return g

        hg = QWidget(); lg = QHBoxLayout(hg); lg.setContentsMargins(0, 0, 0, 0)
        lg.addWidget(self.b_google); lg.addWidget(self.lbl_google); lg.addStretch(1)
        grille.addWidget(section("Quand me prévenir",
                                 "Le jour : une alarme qui sonne. La nuit : un rappel silencieux dans ton agenda.",
                                 list(lignes_sommeil) + [
                                     ("Si ça finit le jour", self.jour),
                                     ("Si ça finit la nuit", self.nuit),
                                     ("Sonner", self.av),
                                     ("Alarmes pour ce qui finit dans", self.fen)]), 0, 0)
        grille.addWidget(section("Agenda",
                                 "Les rappels vont dans l'agenda Google de ce compte (synchro automatique).",
                                 [("Où", self.agenda), ("Compte", self.compte), (None, hg),
                                  ("Notification", self.rappel), ("Rappels jusqu'à", self.hor)]), 0, 1)
        grille.addWidget(section("Téléphone", "Le téléphone branché en USB (débogage USB activé).",
                                 [ligne_tel, (None, self.rel)]), 1, 0)
        grille.addWidget(section("Mode automatique",
                                 "Quand « Automatique » est allumé, le programme lit le jeu tout seul — seulement "
                                 "si Clash of Clans est ouvert sur le village et que tu ne touches pas le téléphone.",
                                 [("Lire le jeu toutes les", self.inter), (None, self.auto_env), (None, self.potions)]), 1, 1)
        grille.addWidget(section("Tes assistants et boosts",
                                 "Lus tout seuls dans l'export du jeu. À changer seulement si c'est faux.",
                                 [("Apprenti ouvrier", self.app), ("Assistant du labo", self.ass), (None, self.cumul),
                                  ("Reconnaissance des icônes", self.seuil)]), 2, 0, 1, 2)
        grille.setRowStretch(3, 1)
        zone.setWidget(interieur)
        racine.addWidget(zone, 1)
        bas = QHBoxLayout()
        bas.addStretch(1)
        b = BoutonCoC("Enregistrer", "vert", "ok", 18)
        b.clicked.connect(self._enregistrer)
        bas.addWidget(b)
        racine.addLayout(bas)

    def _enregistrer(self):
        self.quand_enregistre(self.valeurs())

    def connecter_google(self):
        if not agenda.CLIENT_FILE.exists():
            QMessageBox.information(self, "Google Agenda", AIDE_GOOGLE)
            fic, _ = QFileDialog.getOpenFileName(self, "Fichier client OAuth (client_secret….json)", str(Path.home() / "Téléchargements"), "JSON (*.json)")
            if not fic:
                return
        else:
            fic = None
        self.lbl_google.setText("regarde ton navigateur…")
        QApplication.processEvents()
        try:
            agenda.AgendaGoogle.connecter(fic)
            self.lbl_google.setText("connecté")
        except Exception as e:
            self.lbl_google.setText("échec")
            QMessageBox.warning(self, "Google Agenda", f"Connexion ratée : {e}")

    def valeurs(self):
        self.cfg.update(niveau_apprenti=self.app.value(), niveau_assistant=self.ass.value(),
                        potion_snack_cumul=self.cumul.isChecked(), fenetre_heures=self.fen.value(),
                        avance_minutes=self.av.value(), relancer_coc=self.rel.isChecked(),
                        seuil_icone_hist=self.seuil.value(), mode_jour=self.jour.currentData(),
                        mode_nuit=self.nuit.currentData(), agenda=self.agenda.currentData(),
                        agenda_compte=self.compte.text().strip(), horizon_event_h=self.hor.value(),
                        auto_envoi=self.auto_env.isChecked(), rappel_avant_min=self.rappel.value(),
                        auto_export_min=self.inter.value(), lire_potions=self.potions.isChecked())
        return self.cfg


AIDE_GOOGLE = """Une seule fois, pour que le programme puisse écrire dans ton Google Agenda :

1. Va sur https://console.cloud.google.com/ (avec ton compte Google)
2. Crée un projet (ex : « coc-alarmes »)
3. « API et services » → « Bibliothèque » → active « Google Calendar API »
4. « Écran de consentement OAuth » → Externe → ajoute ton adresse Gmail comme utilisateur test
5. « Identifiants » → « Créer » → « ID client OAuth » → type « Application de bureau »
6. Télécharge le JSON

Ensuite choisis ce fichier ici : ton navigateur s'ouvre, tu acceptes, c'est fini.
(Autre choix sans tout ça : Agenda = « Agenda du téléphone ».)"""


# --------------------------------------------------------------------------
TYPES_ORDRE = [k for k in BOOST_TYPES if k != "ignorer"] + ["ignorer"]


class Fenetre(QMainWindow):
    sig_presse_papier = pyqtSignal(str)
    COL_A = ["✓", "Nom", "Catégorie", "Affiché (modifiable)", "Vitesse", "Fin estimée", "Dans", "Note"]
    COL_B = ["✓", "Icône", "Type", "Reste", "Commence dans", "Cible (apprenti/assistant)", "Info"]
    COL_C = ["Heure", "Dans", "Moment", "Alarme", "Agenda", "Nom de l'alarme"]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CoC Alarmes")
        self.resize(1500, 920)
        self.cfg = load_config()
        self.adb = Adb(self.cfg.get("serial", ""))
        self.ams: list[Amelioration] = []
        self.boosts: list[Boost] = []
        self.icones: dict[int, Image.Image] = {}
        self.dernier_scan = None
        self.sorties = []
        self.plans = []
        self.noms = Noms()
        self.dernier_export_ts = int(load_json(STATE_FILE, {}).get("dernier_export_ts", 0))
        self.pont = None
        self._export_auto_en_cours = False
        self._envoi_en_cours = False
        self.base_plan = None
        self.workers = []
        self.suivi_worker: SuiviWorker | None = None
        self._maj = False
        self._t_precis = 0.0

        # ---------- widgets partagés entre pages (créés avant les pages)
        self.cb_tel = QComboBox(); self.cb_tel.setMinimumWidth(180)
        self.b_ref = QPushButton("Rechercher"); self.b_ref.setIcon(theme.icone("maj", theme.TEXTE_2))
        self.b_ref.setToolTip("Rechercher les téléphones branchés"); self.b_ref.clicked.connect(self.chercher_tel)
        self.lbl_tel = QLabel("")
        self.cb_cat = QComboBox(); self.cb_cat.addItems(["Onglet du menu : auto"] + [f"Onglet du menu : {theme.NOM_CATEGORIE[c]}" for c in CATEGORIES])
        self.cb_cat.setToolTip("Quel onglet du menu des ouvriers est ouvert dans le jeu. « auto » = lu dans le titre.")
        self.b_scan = QPushButton("Lire l'écran"); self.b_scan.setIcon(theme.icone("camera", theme.TEXTE_2))
        self.b_scan.clicked.connect(self.scanner)
        self.b_scan.setToolTip("Lit le menu des ouvriers ouvert dans le jeu (2 s). Précision : la minute.")
        self.b_precis = QPushButton("Lecture précise"); self.b_precis.setIcon(theme.icone("cible", theme.TEXTE_2))
        self.b_precis.clicked.connect(self.scan_precis)
        self.b_precis.setToolTip("Relit l'écran en boucle et repère l'instant exact où chaque minute change :\n"
                                 "précision ±1 s et vitesse réelle mesurée (vérifie les potions).\n"
                                 "≤ 15 s avec potion, ≤ 70 s sans. Garde le menu des ouvriers ouvert.")
        self.prog = QProgressBar(); self.prog.setMaximumWidth(200); self.prog.setVisible(False)
        self.b_ouvrir = QPushButton("Ouvrir une capture…"); self.b_ouvrir.setIcon(theme.icone("image", theme.TEXTE_2))
        self.b_ouvrir.clicked.connect(self.ouvrir_image)
        self.b_autoexp = QPushButton("Relire le jeu (sans envoyer)"); self.b_autoexp.setIcon(theme.icone("maj", theme.TEXTE_2))
        self.b_autoexp.setToolTip("Va chercher l'export dans le jeu (~25 s) mais n'envoie rien au téléphone.")
        self.b_autoexp.clicked.connect(lambda: self.export_auto(auto=False))
        self.b_export = QPushButton("Coller un export"); self.b_export.setIcon(theme.icone("presse", theme.TEXTE_2))
        self.b_export.clicked.connect(lambda: self.importer_presse_papier(manuel=True))
        self.b_export.setToolTip("Si tu as copié l'export toi-même (profil → Paramètres → Paramètres supplémentaires → Copier).")
        self.chk_auto = Interrupteur("Automatique")
        self.chk_auto.setToolTip("Lit le jeu tout seul de temps en temps et met à jour alarmes + agenda.\n"
                                 "Seulement si Clash of Clans est ouvert sur le village et que tu ne touches pas le téléphone.")
        self.chk_auto.setChecked(bool(load_json(STATE_FILE, {}).get("auto", False)))
        self.chk_auto.toggled.connect(self._auto_change)
        self.c_reveil = QTimeEdit(QTime.fromString(self.cfg["eveil_debut"], "HH:mm")); self.c_reveil.setDisplayFormat("HH:mm")
        self.c_coucher = QTimeEdit(QTime.fromString(self.cfg["eveil_fin"], "HH:mm")); self.c_coucher.setDisplayFormat("HH:mm")
        for w in (self.c_reveil, self.c_coucher):
            w.timeChanged.connect(self._conseil_regle)

        # ---------- squelette : navigation à gauche + pages
        racine = QWidget(); racine.setObjectName("racine")
        self.setCentralWidget(racine)
        h = QHBoxLayout(racine); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(0)
        nav = QWidget(); nav.setObjectName("nav"); nav.setFixedWidth(232)
        nv = QVBoxLayout(nav); nv.setContentsMargins(theme.E3, theme.E6, theme.E3, theme.E4); nv.setSpacing(theme.E1)
        logo = QWidget(); ll = QHBoxLayout(logo); ll.setContentsMargins(theme.E2, 0, 0, 0); ll.setSpacing(theme.E2)
        il = QLabel(); il.setPixmap(theme.icone_appli().pixmap(34, 34)); ll.addWidget(il)
        ll.addWidget(TexteCoC("CoC Alarmes", 22, theme.OR, 2.6)); ll.addStretch(1)
        sl = QLabel("Fin des améliorations\n→ alarmes et agenda"); sl.setObjectName("sous_logo")
        nv.addWidget(logo); nv.addWidget(sl); nv.addSpacing(theme.E6)
        self.nav_btns = []
        for i, (ic, txt) in enumerate((("accueil", "Accueil"), ("conseils", "Conseils"), ("outils", "Outils"), ("reglages", "Réglages"))):
            b = QPushButton("  " + txt); b.setObjectName("navbtn"); b.setCheckable(True); b.setAutoExclusive(True)
            b.setIcon(theme.icone(ic, theme.TEXTE_2, 20)); b.setIconSize(QSize(20, 20))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c, i=i: self.aller(i))
            nv.addWidget(b); self.nav_btns.append(b)
        nv.addStretch(1)
        carte_auto = QFrame(); carte_auto.setObjectName("carte")
        ca = QVBoxLayout(carte_auto); ca.setContentsMargins(theme.E3, theme.E3, theme.E3, theme.E3); ca.setSpacing(theme.E2)
        self.pill_tel = QLabel(""); self.pill_tel.setWordWrap(True)
        ca.addWidget(self.pill_tel)
        ca.addWidget(self.chk_auto)
        self.lbl_auto = QLabel(""); self.lbl_auto.setObjectName("petit"); self.lbl_auto.setWordWrap(True)
        ca.addWidget(self.lbl_auto)
        nv.addWidget(carte_auto)
        h.addWidget(nav)

        contenu = QWidget(); contenu.setObjectName("page")
        cv = QVBoxLayout(contenu); cv.setContentsMargins(theme.E6, theme.E6, theme.E6, theme.E4)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._page_accueil())
        self.pages.addWidget(self._page_conseils())
        self.pages.addWidget(self._page_outils())
        self.reglages = FormReglages(self.cfg,
                                     [("Je me réveille à", self.c_reveil), ("Je me couche à", self.c_coucher)],
                                     ("Téléphone", self._ligne_tel()), self.enregistrer_reglages)
        self.pages.addWidget(self.reglages)
        cv.addWidget(self.pages, 1)
        h.addWidget(contenu, 1)
        self.aller(0)

        self.timer = QTimer(self); self.timer.timeout.connect(self.recalculer); self.timer.start(5000)
        self.t_sec = QTimer(self); self.t_sec.timeout.connect(self._tic_vedette); self.t_sec.start(1000)
        self.setWindowIcon(theme.icone_appli())
        self._demarrage = time.time()
        self._notifies = set()
        self._quitter = False
        self._prevenu_fond = False
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(theme.icone_appli(), self)
            m = QMenu()
            m.addAction("Ouvrir CoC Alarmes", self._montrer)
            m.addAction("Mettre à jour", self.mettre_a_jour)
            m.addSeparator()
            m.addAction("Quitter", self._quitter_vraiment)
            self.tray.setContextMenu(m)
            self.tray.activated.connect(lambda r: self._montrer() if r == QSystemTrayIcon.ActivationReason.Trigger else None)
            self.tray.setToolTip("CoC Alarmes")
            self.tray.show()
        self.chercher_tel()
        self.sig_presse_papier.connect(self._presse_papier_tel)
        self.t_auto = QTimer(self); self.t_auto.timeout.connect(lambda: self.export_auto(auto=True))
        self.journal("Prêt. Ouvre Clash of Clans sur ton village, puis « Mettre à jour ».")
        self._auto_change(self.chk_auto.isChecked())
        self._filtre_change()

    # ------------------------------------------------------------------ construction
    def _ligne_tel(self):
        w = QWidget(); l = QHBoxLayout(w); l.setContentsMargins(0, 0, 0, 0)
        l.addWidget(self.cb_tel, 1); l.addWidget(self.b_ref)
        return w

    def aller(self, i):
        self.pages.setCurrentIndex(i)
        for j, b in enumerate(self.nav_btns):
            b.setChecked(j == i)
            b.setIcon(theme.icone(("accueil", "conseils", "outils", "reglages")[j], theme.OR if j == i else theme.TEXTE_2, 20))
        if i == 1:
            self.calculer_conseils()

    def _page_accueil(self):
        page = QWidget(); page.setObjectName("page")
        v = QVBoxLayout(page); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(theme.E4)
        # carte d'état : où on en est + LE bouton principal
        self.carte_etat = QFrame(); self.carte_etat.setObjectName("carte")
        he = QHBoxLayout(self.carte_etat); he.setContentsMargins(theme.E6, theme.E4, theme.E6, theme.E4); he.setSpacing(theme.E4)
        self.etat_icone = QLabel(); self.etat_icone.setFixedSize(52, 52)
        he.addWidget(self.etat_icone, 0, Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout(); col.setSpacing(theme.E1)
        self.etat_titre = TexteCoC("", 28)
        self.statut = QLabel(""); self.statut.setObjectName("aide"); self.statut.setWordWrap(True)
        col.addWidget(self.etat_titre); col.addWidget(self.statut)
        he.addLayout(col, 1)
        self.b_maj = BoutonCoC("Mettre à jour", "vert", "maj", 20)
        self.b_maj.setToolTip("Lit les temps exacts dans le jeu (~25 s, ne touche pas le téléphone),\n"
                              "puis met tes alarmes et ton agenda à jour.")
        self.b_maj.clicked.connect(self.mettre_a_jour)
        self.b_alarmes = BoutonCoC("Envoyer", "gris", "envoyer", 16)
        self.b_alarmes.setToolTip("Envoie au téléphone ce qui est affiché, sans relire le jeu.")
        self.b_alarmes.clicked.connect(lambda: self.envoyer(auto=False))
        he.addWidget(self.b_alarmes, 0, Qt.AlignmentFlag.AlignVCenter)
        he.addWidget(self.b_maj, 0, Qt.AlignmentFlag.AlignVCenter)
        v.addWidget(self.carte_etat)

        # tuiles : prochaine fin + quand chaque bâtiment se libère
        self.tuiles = QHBoxLayout(); self.tuiles.setSpacing(theme.E3)
        v.addLayout(self.tuiles)
        self._sig_tuiles = None
        self._vedette = None

        # filtre : pour quoi être prévenu
        hf = QHBoxLayout(); hf.setSpacing(theme.E2)
        hf.addWidget(TexteCoC("Me prévenir pour", 17, theme.OR, 2.2))
        hf.addSpacing(theme.E2)
        self.b_presets = []
        for txt, cats in (("Village principal", VILLAGE_PRINCIPAL), ("Base des ouvriers", ["Base ouvriers"]), ("Tout", list(CATEGORIES))):
            b = QPushButton(txt); b.setObjectName("segment"); b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c, cats=cats: self._set_filtre(cats))
            hf.addWidget(b); self.b_presets.append((b, set(cats)))
        hf.addSpacing(theme.E4)
        self.b_cats = {}
        for c in CATEGORIES:
            b = QPushButton(theme.NOM_CATEGORIE[c]); b.setObjectName("puce"); b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setChecked(c in self.cfg.get("categories_actives", CATEGORIES))
            b.setToolTip(f"Allumer / éteindre les rappels : {theme.NOM_CATEGORIE[c]}")
            b.toggled.connect(self._filtre_change)
            self.b_cats[c] = b
            hf.addWidget(b)
        hf.addStretch(1)
        v.addLayout(hf)

        # liste des fins + frise
        corps = QHBoxLayout(); corps.setSpacing(theme.E4)
        carte_liste = QFrame(); carte_liste.setObjectName("carte")
        cl = QVBoxLayout(carte_liste); cl.setContentsMargins(theme.E4, theme.E4, theme.E3, theme.E4); cl.setSpacing(theme.E2)
        tl = QHBoxLayout()
        tl.addWidget(TexteCoC("Prochaines fins", 20, theme.OR, 2.4)); tl.addStretch(1)
        tl.addWidget(legende("alarme", theme.VERT, "alarme")); tl.addWidget(legende("agenda", theme.ORANGE, "agenda"))
        tl.addWidget(legende("lune", theme.ORANGE, "la nuit"))
        cl.addLayout(tl)
        zone = QScrollArea(); zone.setWidgetResizable(True); zone.setFrameShape(QFrame.Shape.NoFrame)
        zone.setObjectName("transparent"); zone.viewport().setObjectName("transparent")
        self.liste_fins = QWidget(); self.liste_fins.setObjectName("transparent")
        self.liste_v = QVBoxLayout(self.liste_fins); self.liste_v.setContentsMargins(0, 0, theme.E2, 0); self.liste_v.setSpacing(theme.E2)
        self.liste_v.addStretch(1)
        zone.setWidget(self.liste_fins)
        self._fondu = QGraphicsOpacityEffect(self.liste_fins); self._fondu.setOpacity(1.0)
        self.liste_fins.setGraphicsEffect(self._fondu)
        self._anim_fondu = QPropertyAnimation(self._fondu, b"opacity", self)
        self._anim_fondu.setDuration(240); self._anim_fondu.setStartValue(0.25); self._anim_fondu.setEndValue(1.0)
        self._anim_fondu.setEasingCurve(QEasingCurve.Type.OutCubic)
        cl.addWidget(zone, 1)
        corps.addWidget(carte_liste, 3)
        carte_chrono = QFrame(); carte_chrono.setObjectName("carte")
        cc = QVBoxLayout(carte_chrono); cc.setContentsMargins(theme.E4, theme.E4, theme.E4, theme.E4)
        cc.addWidget(TexteCoC("La frise", 20, theme.OR, 2.4))
        self.chrono = Chronologie()
        cc.addWidget(self.chrono, 1)
        corps.addWidget(carte_chrono, 2)
        v.addLayout(corps, 1)
        self._sig_fins = None
        self._dans_labels = []
        return page

    def _page_outils(self):
        page = QWidget(); page.setObjectName("page")
        v = QVBoxLayout(page); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(theme.E3)
        v.addWidget(titre("Outils"))
        a = QLabel("Pour les cas spéciaux. Ici tu peux lire l'écran du jeu (pour voir une potion active), "
                   "coller un export copié à la main, corriger une ligne ou ajouter un boost.")
        a.setObjectName("aide"); a.setWordWrap(True); v.addWidget(a)
        barre = QHBoxLayout(); barre.setSpacing(theme.E2)
        for w in (self.b_autoexp, self.b_export):
            barre.addWidget(w)
        barre.addSpacing(theme.E4)
        for w in (self.cb_cat, self.b_scan, self.b_precis, self.prog, self.b_ouvrir):
            barre.addWidget(w)
        barre.addStretch(1)
        v.addLayout(barre)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.vue = VueEcran()
        carte_vue = QFrame(); carte_vue.setObjectName("carte")
        lv = QVBoxLayout(carte_vue); lv.setContentsMargins(theme.E2, theme.E2, theme.E2, theme.E2); lv.addWidget(self.vue)
        split.addWidget(carte_vue)
        self.onglets = QTabWidget()
        g1, g2, g3 = self._groupes_outils()
        self.onglets.addTab(g1, "Améliorations")
        self.onglets.addTab(g2, "Boosts")
        self.onglets.addTab(g3, "Envoi au téléphone")
        split.addWidget(self.onglets)
        split.setSizes([480, 900])
        v.addWidget(split, 1)
        v.addWidget(sous_titre("Journal"))
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(140)
        v.addWidget(self.log)
        return page

    def _groupes_outils(self):
        g1 = QWidget(); g1.setObjectName("page")
        l1 = QVBoxLayout(g1)
        self.t_am = self._table(self.COL_A)
        self.t_am.itemChanged.connect(self._am_change)
        l1.addWidget(self.t_am)
        h1 = QHBoxLayout()
        for txt, fn, ic in (("Ajouter une ligne", self.ajouter_am, "plus"), ("Supprimer la ligne", self.suppr_am, "poubelle"),
                            ("Tout vider", self.vider, "poubelle")):
            b = QPushButton(txt); b.setIcon(theme.icone(ic, theme.TEXTE_2)); b.clicked.connect(fn); h1.addWidget(b)
        h1.addStretch(1)
        l1.addLayout(h1)

        g2 = QWidget(); g2.setObjectName("page")
        l2 = QVBoxLayout(g2)
        a = QLabel("Les potions actives se voient dans les bulles à côté du menu des ouvriers : « Lire l'écran ». "
                   "Tu peux aussi en ajouter une ici.")
        a.setObjectName("aide"); a.setWordWrap(True); l2.addWidget(a)
        self.t_bo = self._table(self.COL_B)
        self.t_bo.itemChanged.connect(self._bo_change)
        self.t_bo.verticalHeader().setDefaultSectionSize(44)
        l2.addWidget(self.t_bo)
        h2 = QHBoxLayout()
        for txt, typ in (("Apprenti ouvrier", "apprenti"), ("Assistant du labo", "assistant"), ("Potion ou autre boost", "potion_ouvrier")):
            b = QPushButton(txt); b.setIcon(theme.icone("plus", theme.TEXTE_2))
            b.clicked.connect(lambda _c, typ=typ: self.ajouter_boost(typ)); h2.addWidget(b)
        b = QPushButton("Supprimer le boost"); b.setIcon(theme.icone("poubelle", theme.TEXTE_2)); b.clicked.connect(self.suppr_boost); h2.addWidget(b)
        h2.addStretch(1)
        l2.addLayout(h2)

        g3 = QWidget(); g3.setObjectName("page")
        l3 = QVBoxLayout(g3)
        self.t_al = self._table(self.COL_C)
        l3.addWidget(self.t_al)
        h3 = QHBoxLayout()
        self.chk_suppr = QCheckBox("Avant, enlever mes anciennes alarmes « CoC: »")
        self.chk_suppr.setToolTip("Seulement les alarmes dont le nom commence par « CoC: ». Tes autres alarmes ne sont jamais touchées.")
        self.chk_suppr.setChecked(bool(load_json(STATE_FILE, {}).get("suppr_ok", True)))
        h3.addWidget(self.chk_suppr)
        b = QPushButton("Voir les alarmes sur le téléphone"); b.clicked.connect(self.voir_alarmes); h3.addWidget(b)
        b = QPushButton("Enlever toutes les alarmes CoC"); b.setObjectName("danger"); b.clicked.connect(self.nettoyer); h3.addWidget(b)
        h3.addStretch(1)
        l3.addLayout(h3)
        return g1, g2, g3

    def _page_conseils(self):
        page = QWidget(); page.setObjectName("page")
        v = QVBoxLayout(page); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(theme.E3)
        v.addWidget(titre("Conseils"))
        intro = QLabel("Quand boire une potion d'ouvrier et sur quoi mettre l'apprenti, pour que tes améliorations "
                       "finissent quand tu es réveillé (heures dans Réglages). Potion = x10 pendant 1 h. "
                       "Apprenti niveau N = +N h sur une seule amélioration.")
        intro.setObjectName("aide"); intro.setWordWrap(True)
        v.addWidget(intro)
        g = QFrame(); g.setObjectName("carte")
        f = QHBoxLayout(g); f.setContentsMargins(theme.E4, theme.E3, theme.E4, theme.E3); f.setSpacing(theme.E3)
        self.c_potions = QSpinBox(); self.c_potions.setRange(0, 20); self.c_potions.setValue(int(self.cfg["potions_dispo"]))
        self.c_app = QCheckBox("Apprenti disponible"); self.c_app.setChecked(bool(self.cfg["apprenti_dispo"]))
        self.c_app_dans = QSpinBox(); self.c_app_dans.setRange(0, 23 * 60); self.c_app_dans.setSingleStep(5)
        self.c_app_dans.setValue(int(self.cfg["apprenti_dispo_dans_min"])); self.c_app_dans.setPrefix("dans "); self.c_app_dans.setSuffix(" min")
        self.c_app_dans.setToolTip("0 = maintenant. Rempli tout seul par l'export du jeu.")
        self.c_app_dans.setEnabled(self.c_app.isChecked())
        self.c_niv = QSpinBox(); self.c_niv.setRange(1, 8); self.c_niv.setValue(int(self.cfg["niveau_apprenti"])); self.c_niv.setPrefix("niveau ")
        lp = QLabel("Potions d'ouvrier que j'ai :")
        f.addWidget(lp); f.addWidget(self.c_potions); f.addSpacing(theme.E4)
        f.addWidget(self.c_app); f.addWidget(self.c_app_dans); f.addWidget(self.c_niv)
        f.addStretch(1)
        for w in (self.c_potions, self.c_app_dans, self.c_niv):
            w.valueChanged.connect(self._conseil_regle)
        self.c_app.toggled.connect(self._conseil_regle)
        v.addWidget(g)
        cr = QFrame(); cr.setObjectName("carte")
        lr = QVBoxLayout(cr); lr.setContentsMargins(theme.E4, theme.E4, theme.E4, theme.E4)
        self.c_resume = QLabel("")
        self.c_resume.setWordWrap(True)
        self.c_resume.setTextFormat(Qt.TextFormat.RichText)
        lr.addWidget(self.c_resume)
        v.addWidget(cr)
        v.addWidget(sous_titre("Les plans possibles"))
        self.t_plans = self._table(["Plan", "Potion d'ouvrier", "Apprenti", "Fins la nuit", "Ouvriers qui attendent", "Temps gagné"])
        self.t_plans.itemSelectionChanged.connect(self._plan_choisi)
        v.addWidget(self.t_plans, 2)
        v.addWidget(sous_titre("Ce que ça change"))
        self.t_detail = self._table(["Amélioration", "Fin sans rien faire", "", "Fin avec ce plan", ""])
        v.addWidget(self.t_detail, 3)
        h = QHBoxLayout()
        self.b_fait = BoutonCoC("J'ai fait ce plan dans le jeu", "or", "ok", 16)
        self.b_fait.setToolTip("Ajoute la potion / l'apprenti de ce plan au calcul des alarmes.")
        self.b_fait.clicked.connect(self.plan_fait)
        h.addStretch(1)
        h.addWidget(self.b_fait)
        v.addLayout(h)
        return page

    def _table(self, cols):
        t = QTableWidget(0, len(cols))
        t.setHorizontalHeaderLabels(cols)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setStretchLastSection(True)
        return t

    # ------------------------------------------------------------------ utils
    def journal(self, msg):
        if msg:
            self.log.appendPlainText(f"[{datetime.now():%H:%M:%S}] {msg}")

    def now(self):
        return self.adb.now()

    def lancer(self, fn, args, ok, nom):
        self._occupe(True, nom)
        w = Worker(fn, *args)
        w.fini.connect(lambda r: (self._occupe(False), ok(r)))
        w.erreur.connect(lambda e: (self._occupe(False), self._erreur(e)))
        w.finished.connect(lambda: self.workers.remove(w) if w in self.workers else None)
        self.workers.append(w)
        w.start()

    def _occupe(self, oui, nom=""):
        for b in (self.b_scan, self.b_alarmes, self.b_ouvrir, self.b_export, self.b_autoexp, self.b_maj):
            b.setEnabled(not oui)
        self.b_maj.setText("Lecture du jeu…" if oui else "Mettre à jour")
        self.b_maj.setIcone("sablier" if oui else "maj")
        if hasattr(self, "etat_titre"):
            self.recalculer()
        if self.suivi_worker is None:
            self.b_precis.setEnabled(not oui)
        if oui:
            self.journal(nom + "…")
            QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
        else:
            QApplication.restoreOverrideCursor()

    def _erreur(self, e):
        self._envoi_en_cours = False
        self.journal("ERREUR : " + e.strip().splitlines()[-1])
        QMessageBox.warning(self, "Erreur", e)

    def _need_tel(self):
        if not self.adb.serial:
            self.chercher_tel()
        if not self.adb.serial:
            QMessageBox.warning(self, "Téléphone", "Aucun téléphone ADB. Branche le A54 (débogage USB) ou connecte-le en wifi.")
            return False
        return True

    # ------------------------------------------------------------------ téléphone
    def chercher_tel(self):
        try:
            devs = self.adb.devices()
        except Exception as e:
            devs = []
            self.journal(f"adb : {e}")
        self.cb_tel.blockSignals(True)
        self.cb_tel.clear()
        self.cb_tel.addItems(devs)
        if self.cfg.get("serial") in devs:
            self.cb_tel.setCurrentText(self.cfg["serial"])
        self.cb_tel.blockSignals(False)
        self.adb.serial = self.cb_tel.currentText()
        self.lbl_tel.setText("Téléphone :" if devs else "Téléphone : ❌ aucun")
        try:
            self.cb_tel.currentTextChanged.disconnect()
        except TypeError:
            pass
        self.cb_tel.currentTextChanged.connect(self._tel_change)

    def _tel_change(self, s):
        self.adb.serial = s
        self.cfg["serial"] = s
        save_json(CONFIG_FILE, self.cfg)

    # ------------------------------------------------------------------ scans
    def _cat_forcee(self):
        i = self.cb_cat.currentIndex()
        return None if i == 0 else CATEGORIES[i - 1]

    def scanner(self):
        if not self._need_tel():
            return
        cat, cfg, adb = self._cat_forcee(), dict(self.cfg), self.adb

        def travail():
            adb.synchroniser()
            img, t = adb.screencap()
            return analyser(img, t, cfg, cat)

        self.lancer(travail, (), self._scan_ok, "Capture + lecture")

    def ouvrir_image(self):
        f, _ = QFileDialog.getOpenFileName(self, "Capture d'écran", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if not f:
            return
        cat, cfg = self._cat_forcee(), dict(self.cfg)
        img = Image.open(f).convert("RGB")
        self.lancer(lambda: analyser(img, time.time(), cfg, cat), (), self._scan_ok, "Lecture de l'image")

    def scan_precis(self):
        if self.suivi_worker is not None:
            self.suivi_worker.stop = True
            self.journal("Arrêt demandé…")
            return
        if not self._need_tel():
            return
        w = SuiviWorker(self.adb, dict(self.cfg), self._cat_forcee())
        self._occupe(True, "Scan précis (garde le menu ouvert)")
        self.suivi_worker = w
        self.b_precis.setText("⏹ Stop")
        self.b_precis.setEnabled(True)
        self.prog.setVisible(True)
        self.prog.setRange(0, 0)
        self._t_precis = time.time()
        w.scan.connect(self._scan_ok)
        w.progres.connect(self._progres)
        w.fini.connect(self._precis_fini)
        w.erreur.connect(lambda e: (self._precis_stop(), self._erreur(e)))
        w.start()

    def _progres(self, msg, ok, n):
        if msg:
            self.journal("  " + msg)
        if n:
            self.prog.setRange(0, n)
            self.prog.setValue(ok)
            self.prog.setFormat(f"{ok}/{n} précis · {time.time() - self._t_precis:.0f} s")

    def _precis_stop(self):
        self.suivi_worker = None
        self.b_precis.setText("🎯 Scan précis")
        self.prog.setVisible(False)
        self._occupe(False)

    def _precis_fini(self, suivi: Suivi):
        self._precis_stop()
        n_ok = sum(1 for a in suivi.ams if a.precis)
        self.journal(f"Scan précis fini : {n_ok}/{len(suivi.ams)} ligne(s) à la seconde près ({suivi.lectures} lectures).")
        now = self.now()
        for a in suivi.ams:
            calc = prevoir(a, self.boosts, self.cfg, now).vitesse_actuelle
            if a.vitesse_mesuree:
                if abs(a.vitesse_mesuree - calc) > 0.3 * calc + 0.5:
                    self.journal(f"⚠ {a.nom} : vitesse mesurée x{a.vitesse_mesuree:.1f} mais calculée x{calc:g} → vérifie les boosts !")
                    a.note += f" ⚠ calculée x{calc:g}"
            elif a.vitesse_max and calc > a.vitesse_max * 1.5:
                self.journal(f"⚠ {a.nom} : pas boosté d'après la mesure (≤ x{a.vitesse_max:.1f}) mais calculé x{calc:g} → boost fini ?")
                a.note += f" ⚠ mesuré ≤ x{a.vitesse_max:.1f}"
        self.rafraichir()

    def _scan_ok(self, res):
        self.dernier_scan = res
        try:
            core.DATA_DIR.mkdir(parents=True, exist_ok=True)
            res.image.save(core.DERNIERE_CAPTURE)
        except Exception:
            pass
        for m in res.messages:
            self.journal("⚠ " + m)
        if not res.ameliorations and not res.boosts:
            self.rafraichir()
            return
        cats = {a.categorie for a in res.ameliorations}
        if any(a.source == "export" for a in self.ams if a.categorie in cats):
            # l'export du jeu est exact : on garde ses lignes, le scan sert aux boosts + à vérifier
            for o in res.ameliorations:
                e = next((a for a in self.ams if a.nom == o.nom and a.categorie == o.categorie), None)
                if e is None or o.illisible:
                    continue
                reste_export = core.travail_restant(e, self.boosts, self.cfg, o.capture_epoch)
                if o.pas_s <= 60 and abs(reste_export - o.travail_s) > 120 + o.pas_s:
                    self.journal(f"⚠ {o.nom} : écran {o.affiche} ≠ export ({fmt_duree(reste_export)}) → refais un export")
        else:
            self.ams = [a for a in self.ams if a.categorie not in cats] + res.ameliorations
        self.boosts = [b for b in self.boosts if not b.box]
        self.icones = {}
        for i, b in enumerate(res.boosts):
            self.boosts.append(b)
            self.icones[id(b)] = res.icones[i]
        n_ill = sum(a.illisible for a in res.ameliorations)
        self.journal(f"Lu : {len(res.ameliorations)} amélioration(s) en cours — onglet : "
                     f"{res.categorie_detectee or 'non lu'} — {len(res.boosts)} bulle(s) de boost.")
        if n_ill:
            self.journal(f"⚠ {n_ill} durée(s) illisible(s) : écris-la dans « Affiché ».")
        if any(b.inconnu for b in res.boosts):
            self.journal("⚠ Icône de boost pas reconnue : choisis son type dans « Boosts » (appris pour la suite).")
        self.rafraichir()

    # ------------------------------------------------------------------ filtres
    def _set_filtre(self, cats):
        for c, b in self.b_cats.items():
            b.blockSignals(True); b.setChecked(c in cats); b.blockSignals(False)
        self._filtre_change()

    def _filtre_change(self, *_):
        self.cfg["categories_actives"] = [c for c, b in self.b_cats.items() if b.isChecked()]
        save_json(CONFIG_FILE, self.cfg)
        actives = set(self.cfg["categories_actives"])
        for b, cats in self.b_presets:
            b.setChecked(actives == cats)
        for c, b in self.b_cats.items():
            n = sum(1 for a in self.ams if a.categorie == c)
            nom = theme.NOM_CATEGORIE[c]
            b.setText(f"{nom}  {n}" if n else nom)
            b.setIcon(theme.icone("coche", theme.CATEGORIE[c], 14) if b.isChecked() else theme.icone("barre", theme.TEXTE_3, 14))
        self.rafraichir()

    # ------------------------------------------------------------------ tables
    def rafraichir(self):
        self._maj = True
        noms = [a.nom for a in self.ams]
        t = self.t_am
        t.setRowCount(0)
        t.setRowCount(len(self.ams))
        for i, a in enumerate(self.ams):
            it = QTableWidgetItem()
            it.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            it.setCheckState(Qt.CheckState.Checked if a.actif else Qt.CheckState.Unchecked)
            t.setItem(i, 0, it)
            t.setItem(i, 1, QTableWidgetItem(a.nom))
            if a.source == "export":
                t.setItem(i, 2, item(a.categorie, couleur=COULEURS.get(a.categorie)))
            else:
                cb = QComboBox(); cb.addItems(CATEGORIES); cb.setCurrentText(a.categorie)
                cb.currentTextChanged.connect(lambda txt, a=a: self._set_cat(a, txt))
                t.setCellWidget(i, 2, cb)
            it = QTableWidgetItem(a.affiche)
            if a.illisible:
                it.setBackground(ROUGE)
            t.setItem(i, 3, it)
            for c in (4, 5, 6):
                t.setItem(i, c, item(""))
            coul = ORANGE if ("ILLISIBLE" in a.note or "vérifie" in a.note or "⚠" in a.note) else (VERT if a.precis else None)
            t.setItem(i, 7, item(a.note, couleur=coul))

        t = self.t_bo
        t.setRowCount(0)
        t.setRowCount(len(self.boosts))
        for i, b in enumerate(self.boosts):
            it = QTableWidgetItem()
            it.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            it.setCheckState(Qt.CheckState.Checked if b.actif else Qt.CheckState.Unchecked)
            t.setItem(i, 0, it)
            lab = QLabel()
            ic = self.icones.get(id(b))
            if ic is not None:
                lab.setPixmap(QPixmap.fromImage(pil_to_qimage(ic)).scaled(40, 40, Qt.AspectRatioMode.KeepAspectRatio,
                                                                          Qt.TransformationMode.SmoothTransformation))
            else:
                lab.setText("prévu" if b.prevu else "main")
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            t.setCellWidget(i, 1, lab)
            cb = QComboBox()
            for k in TYPES_ORDRE:
                cb.addItem(BOOST_TYPES[k][0], k)
            if b.inconnu:
                cb.insertItem(0, "❓ Inconnu — choisis", "")
                cb.setCurrentIndex(0)
            else:
                cb.setCurrentIndex(cb.findData(b.type))
            cb.currentIndexChanged.connect(lambda _i, b=b, cb=cb: self._set_type(b, cb.currentData()))
            t.setCellWidget(i, 2, cb)
            t.setItem(i, 3, QTableWidgetItem(fmt_duree(b.reste_s)))
            t.setItem(i, 4, QTableWidgetItem(fmt_duree(b.debut_dans_s) if b.debut_dans_s else "0"))
            if b.type in ("apprenti", "assistant"):
                cc = QComboBox(); cc.addItem("— choisis —", "")
                for nm in noms:
                    cc.addItem(nm, nm)
                cc.setCurrentIndex(max(0, cc.findData(b.cible)))
                cc.currentIndexChanged.connect(lambda _i, b=b, cc=cc: self._set_cible(b, cc.currentData()))
                t.setCellWidget(i, 5, cc)
            else:
                t.setItem(i, 5, item("tout l'onglet concerné" if b.type != "ignorer" else ""))
            info = []
            if b.box:
                info.append(f"lu : {b.texte}")
                info.append("icône reconnue" if not b.inconnu else f"icône inconnue (écart {b.score_icone:.2f})")
            t.setItem(i, 6, item(", ".join(info)))
        self._maj = False
        self.recalculer()
        for tb in (self.t_am, self.t_bo):
            for r in range(tb.rowCount()):
                for c in range(tb.columnCount()):
                    w = tb.cellWidget(r, c)
                    if isinstance(w, QComboBox):
                        w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                        w.wheelEvent = lambda e: e.ignore()
        if self.onglets.currentIndex() == 1:
            self.calculer_conseils()

    def recalculer(self):
        if self._maj:
            return
        self._maj = True
        now = self.now()
        prevs = []
        for i, a in enumerate(self.ams):
            if i >= self.t_am.rowCount() or self.t_am.item(i, 4) is None:
                break
            if a.illisible:
                for c, txt in ((4, ""), (5, "—"), (6, "")):
                    self.t_am.item(i, c).setText(txt)
                continue
            p = prevoir(a, self.boosts, self.cfg, now)
            prevs.append(p)
            vit = f"x{p.vitesse_actuelle:g}"
            if a.vitesse_mesuree:
                vit += f" (mesuré x{a.vitesse_mesuree:.1f})"
            self.t_am.item(i, 4).setText(vit)
            fin = hm(p.fin_epoch)
            if a.pas_s >= 3600:
                fin = "≈ " + fin
            self.t_am.item(i, 5).setText(fin)
            self.t_am.item(i, 6).setText(fmt_duree(p.fin_epoch - now) if p.fin_epoch > now else "fini ✔")
            dans24 = 0 < p.fin_epoch - now < 24 * 3600
            filtre = a.categorie not in self.cfg.get("categories_actives", CATEGORIES)
            if filtre:
                coul = GRIS
            else:
                coul = VERT if (dans24 and a.actif) else self.palette().text().color()
            for c in (1, 3, 4, 5, 6):
                it = self.t_am.item(i, c)
                if it:
                    it.setForeground(coul)
        self.sorties = repartir(prevs, now, self.cfg)
        # chronologie
        envoi = {}
        for so in self.sorties:
            for n in so.noms:
                envoi[n] = ("A" if so.alarme else "") + ("E" if so.event else "")
        actives = set(self.cfg.get("categories_actives", CATEGORIES))
        pts = []
        for p in prevs:
            if p.fin_epoch == float("inf"):
                continue
            a = p.am
            sortie = "filtré" if (a.categorie not in actives or not a.actif) else envoi.get(a.nom, "")
            nom = a.nom + (" ≈" if a.pas_s >= 3600 else "")
            pts.append((p.fin_epoch, nom, a.categorie, sortie))
        ev = Eveil(self.cfg["eveil_debut"], self.cfg["eveil_fin"])
        self.chrono.maj(pts, (ev.d, ev.f))
        # état : ce qui est déjà sur le téléphone
        st = load_json(STATE_FILE, {})
        fut_a = sum(1 for x in st.get("alarmes", []) if x["epoch"] > now)
        fut_e = sum(1 for x in st.get("events", []) if x["epoch"] > now)
        n_a = sum(1 for so in self.sorties if so.alarme)
        n_e = sum(1 for so in self.sorties if so.event)
        a_jour = (sorted((so.label, so.epoch) for so in self.sorties if so.alarme)
                  == sorted((x["label"], x["epoch"]) for x in st.get("alarmes", []) if x["epoch"] > now)
                  and sorted((so.label, so.epoch) for so in self.sorties if so.event)
                  == sorted((x["label"], x["epoch"]) for x in st.get("events", []) if x["epoch"] > now))
        self._notifier(prevs, now)
        self._maj_accueil(prevs, now)
        self._maj_etat(now, n_a, n_e, a_jour, fut_a, fut_e)
        self._maj_nav()
        t = self.t_al
        t.setRowCount(len(self.sorties))
        for i, so in enumerate(self.sorties):
            vals = ((hm(so.epoch), None), (fmt_duree(so.epoch - now), None),
                    ("nuit" if so.nuit else "jour", ORANGE if so.nuit else VERT),
                    ("oui" if so.alarme else "—", None), ("oui" if so.event else "—", None), (so.label, None))
            for c, (txt, coul) in enumerate(vals):
                t.setItem(i, c, item(txt, couleur=coul))
        for i, b in enumerate(self.boosts):
            it = self.t_bo.item(i, 3) if i < self.t_bo.rowCount() else None
            if it is not None and not it.isSelected():
                reste = b.reste_s - max(0.0, now - b.capture_epoch - b.debut_dans_s)
                it.setText(fmt_duree(reste) if reste > 0 else "fini")
        if self.dernier_scan is not None:
            boites = []
            for i, a in enumerate(self.ams):
                if a.box and any(a is x for x in self.dernier_scan.ameliorations):
                    col = ROUGE if a.illisible else (VERT if a.actif else GRIS)
                    boites.append((a.box, col, str(i + 1)))
            for i, b in enumerate(self.boosts):
                if b.box and any(b is x for x in self.dernier_scan.boosts):
                    col = ORANGE if b.inconnu else (BLEU if b.actif else GRIS)
                    boites.append((b.box, col, f"B{i + 1}"))
            self.vue.montrer(self.dernier_scan.image, boites)
        self._maj = False

    # ------------------------------------------------------------------ accueil
    def mettre_a_jour(self):
        if not self.adb.serial:
            self.chercher_tel()
        if not self.adb.serial:
            QMessageBox.warning(self, "Téléphone", "Aucun téléphone trouvé.\nBranche le téléphone en USB "
                                "(débogage USB activé), puis réessaie.")
            return
        self.export_auto(auto=False, envoyer=True)

    def enregistrer_reglages(self, cfg):
        self.cfg = cfg
        self.cfg["eveil_debut"] = self.c_reveil.time().toString("HH:mm")
        self.cfg["eveil_fin"] = self.c_coucher.time().toString("HH:mm")
        self.c_niv.blockSignals(True); self.c_niv.setValue(int(self.cfg["niveau_apprenti"])); self.c_niv.blockSignals(False)
        save_json(CONFIG_FILE, self.cfg)
        if self.chk_auto.isChecked():
            self._auto_change(True)
        self.journal("Réglages enregistrés.")
        self.rafraichir()
        self.aller(0)

    def parametres(self):
        self.aller(3)

    def _maj_accueil(self, prevs, now):
        """Cartes « Prochaines fins », groupées par jour. Refaites seulement si quelque chose change."""
        actives = set(self.cfg.get("categories_actives", CATEGORIES))
        envoi = {}
        nuit = {}
        for so in self.sorties:
            for n in so.noms:
                envoi[n] = (so.alarme, so.event)
                nuit[n] = so.nuit
        ev = Eveil(self.cfg["eveil_debut"], self.cfg["eveil_fin"])
        lignes = []
        for p in sorted(prevs, key=lambda p: p.fin_epoch):
            a = p.am
            if p.fin_epoch == float("inf") or p.fin_epoch < now - 60:
                continue
            filtre = a.categorie not in actives
            al, evn = envoi.get(a.nom, (False, False))
            lignes.append((p, filtre, al, evn, not ev.eveille(p.fin_epoch)))
        self._maj_tuiles(prevs, now)
        sig = tuple((l[0].am.nom, int(l[0].fin_epoch // 60), l[1], l[2], l[3], l[0].am.actif) for l in lignes)
        if sig == self._sig_fins:
            for lab, t in self._dans_labels:
                lab.setText(dans_lisible(t - now))
            return
        premier = self._sig_fins is None or not self._sig_fins
        self._sig_fins = sig
        if premier and lignes:
            self._anim_fondu.stop(); self._anim_fondu.start()
        while self.liste_v.count() > 1:
            it = self.liste_v.takeAt(0)
            if it.widget():
                it.widget().hide()
                it.widget().deleteLater()
        self._dans_labels = []
        if not lignes:
            self.liste_v.insertWidget(0, self._ecran_vide())
            return
        pos = 0
        jour = None
        fen_alarme = min(float(self.cfg.get("fenetre_heures", 24)), 24) * 3600
        for p, filtre, al, evn, la_nuit in lignes:
            j = jour_lisible(p.fin_epoch)
            if j != jour:
                jour = j
                lj = QLabel(j); lj.setObjectName("jour")
                self.liste_v.insertWidget(pos, lj); pos += 1
            self.liste_v.insertWidget(pos, self._carte_fin(p, filtre, al, evn, la_nuit, now, fen_alarme)); pos += 1

    def _carte_fin(self, p, filtre, al, evn, la_nuit, now, fen_alarme):
        a = p.am
        couleur = theme.CATEGORIE.get(a.categorie, theme.TEXTE_2)
        eteint = filtre or not a.actif
        f = QFrame(); f.setObjectName("fin_eteinte" if eteint else "fin")
        h = QHBoxLayout(f); h.setContentsMargins(theme.E3, theme.E2, theme.E4, theme.E2); h.setSpacing(theme.E3)
        h.addWidget(PastilleIcone(theme.ICONE_CATEGORIE.get(a.categorie, "marteau"), couleur, 40, eteint))
        col = QVBoxLayout(); col.setContentsMargins(0, 0, 0, 0); col.setSpacing(2)
        nom = QLabel(a.nom); nom.setObjectName("nom_eteint" if eteint else "nom_fin")
        sous = theme.NOM_CATEGORIE.get(a.categorie, a.categorie)
        if a.pas_s >= 3600:
            sous += " · à 1 h près"
        if "gobelin" in a.note:
            sous += " · ouvrier gobelin"
        ls = QLabel(sous); ls.setObjectName("petit"); ls.setStyleSheet("background: transparent; border: none;")
        col.addWidget(nom); col.addWidget(ls)
        h.addLayout(col, 1)
        # badge : ce qui va se passer
        if not a.actif:
            b = badge(None, "Désactivé", theme.TEXTE_3, "Rallume l'interrupteur pour être prévenu.")
        elif filtre:
            b = badge(None, "Catégorie éteinte", theme.TEXTE_3, "Rallume-la dans « Me prévenir pour ».")
        elif al and evn:
            b = badge("alarme", "Alarme + agenda", theme.VERT)
        elif al:
            b = badge("alarme", "Alarme", theme.VERT, "Une alarme sonnera sur le téléphone.")
        elif evn:
            b = badge("agenda", "Agenda", theme.ORANGE, "Rappel silencieux dans ton agenda Google (pas d'alarme).")
        elif p.fin_epoch - now > fen_alarme and "alarme" in self.cfg.get("mode_nuit" if la_nuit else "mode_jour", ""):
            b = badge("sablier", "Alarme plus tard", theme.TEXTE_2,
                      "L'Horloge Samsung n'accepte que les alarmes à moins de 24 h :\n"
                      "elle sera mise à la prochaine mise à jour.")
        else:
            b = badge(None, "Pas de rappel", theme.TEXTE_3)
        h.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
        # heure, façon jeu
        ch = QVBoxLayout(); ch.setContentsMargins(0, 0, 0, 0); ch.setSpacing(0)
        lh = QHBoxLayout(); lh.setSpacing(theme.E1); lh.addStretch(1)
        if la_nuit:
            ic = QLabel(); ic.setPixmap(theme.pixmap("lune", theme.ORANGE, 16)); ic.setToolTip("Finit la nuit")
            ic.setStyleSheet("background: transparent; border: none;")
            lh.addWidget(ic)
        heure = TexteCoC(datetime.fromtimestamp(p.fin_epoch).strftime("%H:%M"), 24,
                         theme.TEXTE_3 if eteint else theme.TEXTE, 2.6, Qt.AlignmentFlag.AlignRight)
        heure.setFixedWidth(86)
        lh.addWidget(heure)
        ch.addLayout(lh)
        dans = QLabel(dans_lisible(p.fin_epoch - now)); dans.setObjectName("petit")
        dans.setAlignment(Qt.AlignmentFlag.AlignRight); dans.setStyleSheet("background: transparent; border: none;")
        ch.addWidget(dans)
        self._dans_labels.append((dans, p.fin_epoch))
        h.addLayout(ch)
        sw = Interrupteur(); sw.setChecked(a.actif)
        sw.setToolTip("Allumé = je suis prévenu pour celle-ci.")
        sw.toggled.connect(lambda on, a=a: QTimer.singleShot(170, lambda: self._basculer(a, on)))
        h.addWidget(sw)
        return f

    def _maj_tuiles(self, prevs, now):
        """Tuile vedette (prochaine fin, compte à rebours) + une tuile par catégorie : quand ça se libère."""
        actives = set(self.cfg.get("categories_actives", CATEGORIES))
        futurs = [p for p in prevs if p.fin_epoch != float("inf") and p.fin_epoch > now]
        par_cat = {}
        for p in sorted(futurs, key=lambda p: p.fin_epoch):
            par_cat.setdefault(p.am.categorie, []).append(p)
        prochain = next((p for p in sorted(futurs, key=lambda p: p.fin_epoch)
                         if p.am.actif and p.am.categorie in actives), None)
        cats = [c for c in CATEGORIES if c in par_cat]
        sig = (tuple(cats), prochain.am.nom if prochain else None)
        if sig != self._sig_tuiles:
            self._sig_tuiles = sig
            while self.tuiles.count():
                it = self.tuiles.takeAt(0)
                if it.widget():
                    it.widget().hide(); it.widget().deleteLater()
            self._tuile_labels = {}
            if not futurs:
                return
            # vedette
            f = QFrame(); f.setObjectName("tuile_vedette")
            l = QVBoxLayout(f); l.setContentsMargins(theme.E4, theme.E3, theme.E4, theme.E3); l.setSpacing(2)
            h = QHBoxLayout(); h.setSpacing(theme.E2)
            ic = QLabel(); ic.setPixmap(theme.pixmap("sablier", theme.OR, 18)); h.addWidget(ic)
            t = QLabel("Prochaine fin"); t.setObjectName("petit"); t.setStyleSheet(f"color: {theme.OR}; font-weight: 800;")
            h.addWidget(t); h.addStretch(1); l.addLayout(h)
            self._vedette_temps = TexteCoC("", 30, theme.TEXTE, 3.0)
            l.addWidget(self._vedette_temps)
            self._vedette_nom = QLabel(""); self._vedette_nom.setObjectName("aide")
            l.addWidget(self._vedette_nom)
            self.tuiles.addWidget(f, 3)
            for c in cats:
                f = QFrame(); f.setObjectName("tuile")
                l = QVBoxLayout(f); l.setContentsMargins(theme.E4, theme.E3, theme.E4, theme.E3); l.setSpacing(2)
                h = QHBoxLayout(); h.setSpacing(theme.E2)
                h.addWidget(PastilleIcone(theme.ICONE_CATEGORIE[c], theme.CATEGORIE[c], 26, c not in actives))
                t = QLabel(theme.NOM_LIBRE[c]); t.setObjectName("petit"); t.setStyleSheet("font-weight: 800;")
                h.addWidget(t); h.addStretch(1); l.addLayout(h)
                gros = TexteCoC("", 22, theme.CATEGORIE[c] if c in actives else theme.TEXTE_3, 2.4)
                l.addWidget(gros)
                det = QLabel(""); det.setObjectName("petit")
                l.addWidget(det)
                self.tuiles.addWidget(f, 2)
                self._tuile_labels[c] = (gros, det)
        self._prochain = prochain
        for c, (gros, det) in getattr(self, "_tuile_labels", {}).items():
            liste = par_cat.get(c, [])
            if not liste:
                continue
            p = liste[0]
            gros.setText(dans_lisible(p.fin_epoch - now).replace("dans ", ""))
            n = len(liste)
            quoi = {"Ouvriers": "au travail", "Labo": "en recherche", "Animalerie": "en cours",
                    "Base ouvriers": "en cours", "Forge": "en cours", "Assistants": ""}[c]
            det.setText(f"à {datetime.fromtimestamp(p.fin_epoch):%H:%M}" + (f" · {n} {quoi}" if quoi else ""))
        self._tic_vedette()

    def _tic_vedette(self):
        p = getattr(self, "_prochain", None)
        if p is None or not hasattr(self, "_vedette_temps"):
            return
        try:
            reste = max(0, int(p.fin_epoch - self.now()))
            h, r = divmod(reste, 3600)
            m, s = divmod(r, 60)
            self._vedette_temps.setText(f"{h} h {m:02d} min {s:02d} s" if h else f"{m} min {s:02d} s")
            self._vedette_nom.setText(f"{p.am.nom} · à {datetime.fromtimestamp(p.fin_epoch):%H:%M}")
        except RuntimeError:
            pass   # tuile en cours de remplacement

    def _notifier(self, prevs, now):
        """Notification du bureau quand une amélioration finit (si l'appli tourne)."""
        if self.tray is None:
            return
        actives = set(self.cfg.get("categories_actives", CATEGORIES))
        prochain = None
        for p in prevs:
            a = p.am
            if p.fin_epoch == float("inf"):
                continue
            if p.fin_epoch > now and (prochain is None or p.fin_epoch < prochain.fin_epoch):
                prochain = p
            cle = (a.nom, int(p.fin_epoch // 60))
            if (a.actif and a.categorie in actives and self._demarrage < p.fin_epoch <= now
                    and now - p.fin_epoch < 600 and cle not in self._notifies):
                self._notifies.add(cle)
                self.tray.showMessage("Amélioration terminée", f"{a.nom} ({theme.NOM_CATEGORIE.get(a.categorie, '')}) est fini !",
                                      theme.icone_appli(), 8000)
        if prochain:
            self.tray.setToolTip(f"CoC Alarmes — prochaine fin : {prochain.am.nom} à "
                                 f"{datetime.fromtimestamp(prochain.fin_epoch):%H:%M}")

    def _montrer(self):
        self.showNormal(); self.raise_(); self.activateWindow()

    def _quitter_vraiment(self):
        self._quitter = True
        self._nettoyer_et_quitter()

    def _nettoyer_et_quitter(self):
        """Arrête tout proprement puis quitte (même si la fenêtre est cachée dans la barre système)."""
        for t in (self.timer, self.t_sec, getattr(self, "t_auto", None)):
            if t is not None:
                t.stop()
        if self.suivi_worker is not None:
            self.suivi_worker.stop = True
            self.suivi_worker.wait(3000)
        st = load_json(STATE_FILE, {})
        st["suppr_ok"] = self.chk_suppr.isChecked()
        st["auto"] = self.chk_auto.isChecked()
        save_json(STATE_FILE, st)
        if self.pont is not None:
            self.pont.arreter()
        self.adb.arreter_video()
        if self.tray is not None:
            self.tray.hide()
        for w in list(self.workers):
            w.wait(2000)
        QApplication.instance().quit()

    def _ecran_vide(self):
        w = QWidget(); w.setObjectName("transparent")
        v = QVBoxLayout(w); v.setContentsMargins(theme.E6, theme.E8, theme.E6, theme.E6); v.setSpacing(theme.E4)
        ic = QLabel(); ic.setPixmap(theme.pixmap("bouclier", theme.OR_FONCE, 72)); ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(ic)
        v.addWidget(TexteCoC("Prêt à commencer", 24, theme.TEXTE, 2.8, Qt.AlignmentFlag.AlignHCenter))
        tel = bool(self.adb.serial)
        for n, (ok, txt) in enumerate(((tel, "Branche ton téléphone en USB" + (" — c'est bon" if tel else "")),
                                        (False, "Ouvre Clash of Clans sur ton village"),
                                        (False, "Clique « Mettre à jour » (environ 25 s, ne touche pas le téléphone)")), 1):
            ligne = QHBoxLayout(); ligne.setSpacing(theme.E3)
            rond = QLabel(str(n) if not ok else "")
            rond.setFixedSize(30, 30); rond.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if ok:
                rond.setPixmap(theme.pixmap("ok", theme.VERT_TEXTE, 18))
            rond.setStyleSheet(f"background: {theme.VERT if ok else theme.SURFACE_3}; border-radius: 15px;"
                               f" color: {theme.TEXTE}; font-family: '{theme.TITRE}'; font-size: 16px;")
            ligne.addWidget(rond)
            t = QLabel(txt); t.setStyleSheet(f"font-size: 15px; color: {theme.TEXTE if not ok else theme.TEXTE_2};")
            ligne.addWidget(t, 1)
            v.addLayout(ligne)
        v.addStretch(1)
        return w

    def _basculer(self, a, on):
        a.actif = on
        self.rafraichir()

    def _maj_etat(self, now, n_a, n_e, a_jour, fut_a, fut_e):
        occupe = self._export_auto_en_cours or self._envoi_en_cours or self.suivi_worker is not None
        if occupe:
            ic, coul, t = "maj", theme.OR, "Mise à jour en cours…"
            obj = "carte_alerte"
        elif not self.ams:
            ic, coul, t = "attention", theme.OR, "Pas encore de données"
            obj = "carte_alerte"
        elif a_jour:
            ic, coul, t = "ok", theme.VERT, "Tout est à jour"
            obj = "carte_ok"
        else:
            ic, coul, t = "attention", theme.OR, "Pas encore envoyé au téléphone"
            obj = "carte_alerte"
        self.etat_icone.setPixmap(theme.pixmap(ic, coul, 52))
        self.etat_titre.setText(t)
        if self.carte_etat.objectName() != obj:
            self.carte_etat.setObjectName(obj)
            self.carte_etat.style().unpolish(self.carte_etat); self.carte_etat.style().polish(self.carte_etat)
        en_avant = bool(self.ams and not a_jour and not occupe)
        self.b_alarmes.setVariante("or" if en_avant else "gris")
        if not occupe:
            self.b_alarmes.setEnabled(bool(self.ams))
        self.etat_titre.setCouleur(theme.VERT if obj == "carte_ok" else theme.TEXTE)
        morceaux = []
        ts = getattr(self, "export_affiche_ts", 0)
        if ts:
            morceaux.append(f"Temps lus dans le jeu il y a {fmt_duree(now - ts)}.")
        if a_jour and self.ams:
            morceaux.append(f"Sur ton téléphone : {fut_a} alarme(s) et {fut_e} rappel(s) d'agenda.")
        elif self.ams:
            morceaux.append(f"À envoyer : {n_a} alarme(s) et {n_e} rappel(s) d'agenda. Clique « Envoyer ».")
        else:
            morceaux.append("Ouvre Clash of Clans sur ton village, puis clique « Mettre à jour ».")
        self.statut.setText(" ".join(morceaux))

    def _maj_nav(self):
        if self.adb.serial:
            self.pill_tel.setText(f"<span style='color:{theme.VERT}'>●</span> Téléphone branché")
        else:
            self.pill_tel.setText(f"<span style='color:{theme.ROUGE}'>●</span> Pas de téléphone")
        if self.chk_auto.isChecked():
            n = int(self.cfg.get("auto_export_min", 120))
            txt = f"Lit le jeu toutes les {n} min." if n else "Quand tu copies l'export."
            if getattr(self, "t_auto", None) and self.t_auto.isActive():
                txt += f" Prochaine fois : {datetime.fromtimestamp(time.time() + self.t_auto.remainingTime() / 1000):%H:%M}."
            self.lbl_auto.setText(txt)
        else:
            self.lbl_auto.setText("Éteint : clique « Mettre à jour » quand tu veux.")

    # ------------------------------------------------------------------ éditions
    def _am_change(self, it):
        if self._maj:
            return
        i, c = it.row(), it.column()
        if i >= len(self.ams):
            return
        a = self.ams[i]
        if c == 0:
            a.actif = it.checkState() == Qt.CheckState.Checked
        elif c == 1:
            ancien = a.nom
            a.nom = it.text().strip()
            for b in self.boosts:
                if b.cible == ancien:
                    b.cible = a.nom
            self.rafraichir()
            return
        elif c == 3:
            d = parse_duree(it.text())
            if not d:
                self.journal(f"« {it.text()} » : durée pas comprise (ex : 6h 48min, 1j 7h, 39min 31s)")
                self._maj = True
                it.setBackground(ROUGE)
                self._maj = False
                return
            # valeur écrite = ce que le jeu affiche maintenant
            a.travail_s, a.pas_s, a.affiche = d.secondes, d.pas, d.texte
            a.capture_epoch = self.now()
            a.precis = a.illisible = False
            a.actif = True
            a.note = "corrigé à la main"
            self.rafraichir()
            return
        self.recalculer()

    def _set_cat(self, a, txt):
        a.categorie = txt
        self.recalculer()

    def _bo_change(self, it):
        if self._maj:
            return
        i, c = it.row(), it.column()
        if i >= len(self.boosts):
            return
        b = self.boosts[i]
        if c == 0:
            b.actif = it.checkState() == Qt.CheckState.Checked
        elif c in (3, 4):
            txt = it.text().strip()
            now = self.now()
            if c == 4 and txt in ("", "0"):
                val = 0
            else:
                d = parse_duree(txt)
                if not d:
                    self.journal(f"« {txt} » : durée pas comprise")
                    return
                val = d.secondes
            if c == 3:
                b.debut_dans_s = max(0.0, b.capture_epoch + b.debut_dans_s - now)
                b.reste_s = val
            else:
                b.debut_dans_s = val
            b.capture_epoch = now
            self.rafraichir()
            return
        self.recalculer()

    def _set_type(self, b, typ):
        if not typ:
            return
        ic = self.icones.get(id(b))
        if ic is not None and (b.inconnu or typ != b.type):
            apprendre_icone(ic, typ)
            self.journal(f"Icône apprise : {BOOST_TYPES[typ][0]}")
        b.type, b.inconnu = typ, False
        b.actif = typ != "ignorer"
        self.rafraichir()

    def _set_cible(self, b, nom):
        b.cible = nom or ""
        self.recalculer()

    def ajouter_am(self):
        cat = self._cat_forcee() or "Ouvriers"
        self.ams.append(Amelioration("Nouvelle amélioration", cat, "?? (écris la durée)", 0, 60, self.now(),
                                     actif=False, note="à remplir", illisible=True))
        self.rafraichir()

    def suppr_am(self):
        r = self.t_am.currentRow()
        if 0 <= r < len(self.ams):
            del self.ams[r]
            self.rafraichir()

    def vider(self):
        self.ams = []
        self.boosts = [b for b in self.boosts if not b.box]
        self.rafraichir()

    def ajouter_boost(self, typ):
        self.boosts.append(Boost(typ, 3600, self.now(), actif=True))
        self.rafraichir()
        self.journal(f"Boost ajouté : {BOOST_TYPES[typ][0]} — mets le temps restant dans « Reste »"
                     + (" et choisis la cible." if typ in ("apprenti", "assistant") else "."))

    def suppr_boost(self):
        r = self.t_bo.currentRow()
        if 0 <= r < len(self.boosts):
            b = self.boosts.pop(r)
            self.icones.pop(id(b), None)
            self.rafraichir()

    # ------------------------------------------------------------------ conseils
    def _conseil_regle(self, *_):
        self.cfg.update(eveil_debut=self.c_reveil.time().toString("HH:mm"),
                        eveil_fin=self.c_coucher.time().toString("HH:mm"),
                        potions_dispo=self.c_potions.value(), apprenti_dispo=self.c_app.isChecked(),
                        apprenti_dispo_dans_min=self.c_app_dans.value(), niveau_apprenti=self.c_niv.value())
        self.c_app_dans.setEnabled(self.c_app.isChecked())
        save_json(CONFIG_FILE, self.cfg)
        self.calculer_conseils()
        self.recalculer()

    def calculer_conseils(self):
        now = self.now()
        base, plans = conseiller(self.ams, self.boosts, self.cfg, now)
        self.base_plan, self.plans = base, plans
        self.t_plans.setRowCount(0)
        self.t_detail.setRowCount(0)
        if base is None:
            self.c_resume.setText("Aucune amélioration d'ouvrier : fais d'abord un scan du menu des ouvriers.")
            self.b_fait.setEnabled(False)
            return
        incertain = [a.nom for a in self.ams if a.actif and a.categorie == "Ouvriers" and a.pas_s >= 3600]
        txt = (f"<b>Sans rien faire :</b> {base.nuit} amélioration(s) finissent la nuit "
               f"({self.cfg['eveil_fin']}–{self.cfg['eveil_debut']}), temps où des ouvriers attendent : "
               f"<b>{fmt_duree(base.attente_s)}</b>.")
        if plans and (plans[0].attente_s < base.attente_s - 60 or plans[0].gain_s > 60):
            txt += f"<br><span style='color:{theme.OR}'><b>Conseil :</b></span> " + self._phrase(plans[0])
        elif not plans:
            txt += "<br>Pas de potion ni d'apprenti dispo (réglages au-dessus)."
        else:
            txt += "<br>Rien de mieux à faire pour l'instant."
        if incertain:
            txt += (f"<br><i>Précision ±1 h pour : {', '.join(incertain)} (affichage en jours). "
                    f"Refais un scan quand elles passent sous 24 h.</i>")
        self.c_resume.setText(txt)
        rows = [base] + plans
        self.t_plans.setRowCount(len(rows))
        for i, p in enumerate(rows):
            pot = "—"
            if p.potion_t is not None:
                pot = "maintenant" if p.potion_t - now < 300 else hm(p.potion_t)
                if p.potion_plage and p.potion_plage[1] - p.potion_plage[0] >= 900:
                    pot += f"  (ou jusqu'à {hm(p.potion_plage[1])})"
            app = "—" if p.apprenti is None else (
                f"{p.apprenti[0]} ({'maintenant' if p.apprenti[1] - now < 300 else hm(p.apprenti[1])})")
            vals = (p.titre, pot, app, str(p.nuit), fmt_duree(p.attente_s), fmt_duree(p.gain_s) if p.gain_s else "—")
            for c, val in enumerate(vals):
                self.t_plans.setItem(i, c, item(val, couleur=JAUNE if (i == 1 and p.titre.startswith("Meilleur")) else None))
        self.t_plans.selectRow(1 if plans else 0)

    def _phrase(self, p):
        now = self.now()
        morceaux = []
        if p.potion_t is not None:
            quand = "maintenant" if p.potion_t - now < 300 else f"à {hm(p.potion_t)}"
            if p.potion_plage and p.potion_plage[1] - p.potion_plage[0] >= 900:
                quand += f" (ou n'importe quand jusqu'à {hm(p.potion_plage[1])})"
            morceaux.append(f"bois une <b>potion d'ouvrier {quand}</b>")
        if p.apprenti is not None:
            quand = "maintenant" if p.apprenti[1] - now < 300 else f"à {hm(p.apprenti[1])}"
            morceaux.append(f"mets l'<b>apprenti sur {p.apprenti[0]}</b> {quand}")
        return (" et ".join(morceaux) + f" → {p.nuit} fin(s) la nuit, attente {fmt_duree(p.attente_s)}, "
                f"temps gagné {fmt_duree(p.gain_s)}.")

    def _plan_choisi(self):
        r = self.t_plans.currentRow()
        rows = ([self.base_plan] if self.base_plan else []) + self.plans
        if not (0 <= r < len(rows)):
            return
        p, base = rows[r], self.base_plan
        ev = Eveil(self.cfg["eveil_debut"], self.cfg["eveil_fin"])
        noms = sorted(p.fins, key=lambda n: p.fins[n])
        self.t_detail.setRowCount(len(noms))

        def etat(f):
            return ("réveillé", VERT) if ev.eveille(f) else ("la nuit, attend " + fmt_duree(ev.attente(f)), ORANGE)

        for i, n in enumerate(noms):
            fb, fp = base.fins[n], p.fins[n]
            (eb, cb), (ep, cp) = etat(fb), etat(fp)
            for c, (txt, coul) in enumerate(((n, None), (hm(fb), None), (eb, cb), (hm(fp), None), (ep, cp))):
                self.t_detail.setItem(i, c, item(txt, couleur=coul))
        self.b_fait.setEnabled(r > 0)

    def plan_fait(self):
        r = self.t_plans.currentRow()
        if r <= 0 or r - 1 >= len(self.plans):
            return
        p = self.plans[r - 1]
        now = self.now()
        if p.potion_t is not None:
            self.boosts.append(Boost("potion_ouvrier", 3600, now, debut_dans_s=max(0.0, p.potion_t - now), prevu=True))
            self.cfg["potions_dispo"] = max(0, int(self.cfg["potions_dispo"]) - 1)
        if p.apprenti is not None:
            self.boosts.append(Boost("apprenti", 3600, now, cible=p.apprenti[0],
                                     debut_dans_s=max(0.0, p.apprenti[1] - now), prevu=True))
            self.cfg["apprenti_dispo"] = False
        save_json(CONFIG_FILE, self.cfg)
        for w, val in ((self.c_potions, int(self.cfg["potions_dispo"])),):
            w.blockSignals(True); w.setValue(val); w.blockSignals(False)
        self.c_app.blockSignals(True); self.c_app.setChecked(bool(self.cfg["apprenti_dispo"])); self.c_app.blockSignals(False)
        self.journal("Plan ajouté aux boosts. Après l'avoir fait en jeu, refais un « 🎯 Scan précis » pour vérifier.")
        self.onglets.setCurrentIndex(0)
        self.rafraichir()

    # ------------------------------------------------------------------ export du jeu + mode auto
    def _auto_change(self, on):
        st = load_json(STATE_FILE, {})
        st["auto"] = bool(on)
        save_json(STATE_FILE, st)
        if on:
            self.demarrer_pont()
            n = int(self.cfg.get("auto_export_min", 120))
            if n > 0:
                self.t_auto.start(n * 60 * 1000)
                self.journal(f"🤖 Auto : export tout seul toutes les {n} min (si CoC est ouvert sur le village), "
                             "et à chaque fois que tu copies l'export toi-même.")
            else:
                self.journal("🤖 Auto : à chaque fois que tu copies l'export dans le jeu, tout est envoyé.")
        else:
            self.t_auto.stop()

    def demarrer_pont(self):
        """Écoute le presse-papier du TÉLÉPHONE (serveur scrcpy, marche même sous Wayland)."""
        if self.pont is not None and self.pont.actif:
            return True
        if not self.adb.serial:
            return False
        try:
            self.pont = PontPressePapier(self.adb.serial, quand_change=self.sig_presse_papier.emit)
            self.pont.demarrer()
            self.journal("Écoute du presse-papier du téléphone : OK.")
            return True
        except Exception as e:
            self.pont = None
            self.journal(f"⚠ Écoute du presse-papier du téléphone impossible : {e}")
            return False

    def _presse_papier_tel(self, txt):
        if export.est_export(txt) and not self._export_auto_en_cours:
            self.journal("📋 Export copié sur le téléphone : reçu.")
            self.importer_texte(txt, manuel=False, envoi=self.chk_auto.isChecked())

    @staticmethod
    def lire_presse_papier() -> str:
        for cmd in (["wl-paste", "--no-newline"], ["qdbus6", "org.kde.klipper", "/klipper", "getClipboardContents"]):
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
                if r.returncode == 0 and r.stdout.strip():
                    return r.stdout
            except Exception:
                continue
        return QApplication.clipboard().text() or ""

    def importer_presse_papier(self, manuel=True):
        txt = self.lire_presse_papier()
        if not export.est_export(txt) and self.pont is not None and self.pont.dernier:
            txt = self.pont.dernier
        if not export.est_export(txt):
            self.demarrer_pont()
            QMessageBox.information(self, "Export", "Pas d'export du jeu reçu.\n\n"
                                    "Le plus simple : bouton « 🤖 Export auto » (le programme va le chercher tout seul).\n\n"
                                    "Ou à la main dans Clash of Clans : profil → Paramètres → Paramètres supplémentaires → "
                                    "(tout en bas) Exporter les données → Copier. Le programme le reçoit tout seul.")
            return
        self.importer_texte(txt, manuel=True, envoi=False)

    def export_auto(self, auto=False, envoyer=False):
        if self._export_auto_en_cours or self._envoi_en_cours or self.suivi_worker is not None:
            return
        if not self.adb.serial:
            if not auto:
                QMessageBox.warning(self, "Téléphone", "Aucun téléphone branché.")
            return
        if not self.demarrer_pont():
            return
        self._export_auto_en_cours = True
        adb, pont, cfg = self.adb, self.pont, dict(self.cfg)
        notes = []

        def travail():
            t0 = time.time()
            adb.synchroniser()
            ea = ExportAuto(adb, pont, log=notes.append, lire_boosts=bool(cfg.get("lire_potions", True)), cfg=cfg)
            txt, scan = ea.lancer(auto=auto)
            return txt, scan, ea.temps, time.time() - t0

        def ok(r):
            txt, scan, temps, total = r
            self._export_auto_en_cours = False
            for n in notes:
                self.journal(n)
            self.journal(f"Lecture du jeu en {total:.1f} s (" + ", ".join(f"{e} {d:.1f}" for e, d in temps[1:]) + ")")
            if scan is not None:
                self._appliquer_boosts_scan(scan)
            self.importer_texte(txt, manuel=not auto, envoi=auto or envoyer or self.chk_auto.isChecked())

        def ko(e):
            self._export_auto_en_cours = False
            self.journal(f"Export auto pas fait : {e.strip().splitlines()[-1]}")
            if auto:
                self.journal("  → nouvel essai dans 5 min.")
                QTimer.singleShot(5 * 60 * 1000, lambda: self.export_auto(auto=True) if self.chk_auto.isChecked() else None)
            if not auto:
                QMessageBox.warning(self, "Export auto", e)

        self._occupe(True, "Lecture du jeu (ne touche pas le téléphone ~10 s)")
        w = Worker(travail)
        w.fini.connect(lambda r: (self._occupe(False), ok(r)))
        w.erreur.connect(lambda e: (self._occupe(False), ko(e)))
        w.finished.connect(lambda: self.workers.remove(w) if w in self.workers else None)
        self.workers.append(w)
        w.start()

    def _appliquer_boosts_scan(self, res):
        """Potions lues dans les bulles du menu des ouvriers : remplacent celles lues avant."""
        self.dernier_scan = res
        self.boosts = [b for b in self.boosts if not b.box]
        self.icones = {}
        for i, b in enumerate(res.boosts):
            self.boosts.append(b)
            self.icones[id(b)] = res.icones[i]
        connus = [b for b in res.boosts if not b.inconnu and b.actif]
        for b in connus:
            self.journal(f"Boost actif lu à l'écran : {BOOST_TYPES[b.type][0]} — reste {fmt_duree(b.reste_s)}")
        if any(b.inconnu for b in res.boosts):
            self.journal("⚠ Bulle de boost pas reconnue : choisis son type dans Outils → Boosts (appris pour la suite).")
        if not res.boosts:
            self.journal("Pas de potion active.")

    def importer_texte(self, txt, manuel, envoi):
        try:
            ex = export.lire_export(txt, self.noms)
        except Exception as e:
            if manuel:
                self._erreur(f"Export illisible : {e}")
            return
        if not manuel and ex.timestamp <= self.dernier_export_ts:
            return
        self.dernier_export_ts = max(self.dernier_export_ts, ex.timestamp)
        st = load_json(STATE_FILE, {})
        st["dernier_export_ts"] = self.dernier_export_ts
        save_json(STATE_FILE, st)
        if ex.inconnus:
            self.journal(f"Objets inconnus {ex.inconnus} : mise à jour des noms depuis le jeu…")
            base = self.adb._base() if self.adb.serial else None
            self.lancer(lambda: self.noms.mettre_a_jour(base), (),
                        lambda _r: self.appliquer_export(export.lire_export(txt, self.noms), envoi), "Noms du jeu")
            return
        self.appliquer_export(ex, envoi)

    def appliquer_export(self, ex, envoi):
        now = self.now()
        self.export_affiche_ts = ex.timestamp
        self.ams = sorted(ex.ameliorations, key=lambda a: (a.categorie != "Ouvriers", a.travail_s))
        self.dernier_scan = None
        noms = {a.nom for a in self.ams}
        self.boosts = [b for b in self.boosts if not (b.type in ("apprenti", "assistant") and b.cible and b.cible not in noms)]
        age = now - ex.timestamp
        self.journal(f"📋 Export du {datetime.fromtimestamp(ex.timestamp):%d/%m %H:%M:%S} (il y a {fmt_duree(age)}) : "
                     f"{len(self.ams)} amélioration(s) en cours, temps exacts.")
        if ex.niveau_apprenti:
            self.cfg["niveau_apprenti"] = ex.niveau_apprenti
        if ex.niveau_assistant:
            self.cfg["niveau_assistant"] = ex.niveau_assistant
        repos = max(0, (ex.repos_s or 0) - age)
        self.cfg["apprenti_dispo"] = bool(ex.niveau_apprenti)
        self.cfg["apprenti_dispo_dans_min"] = int(repos // 60 + (1 if repos % 60 else 0))
        save_json(CONFIG_FILE, self.cfg)
        for w, val in ((self.c_niv, int(self.cfg["niveau_apprenti"])), (self.c_app_dans, int(self.cfg["apprenti_dispo_dans_min"]))):
            w.blockSignals(True); w.setValue(val); w.blockSignals(False)
        self.c_app.blockSignals(True); self.c_app.setChecked(bool(self.cfg["apprenti_dispo"])); self.c_app.blockSignals(False)
        self.c_app_dans.setEnabled(self.c_app.isChecked())
        if ex.niveau_apprenti:
            self.journal(f"Apprenti niv. {ex.niveau_apprenti}, assistant labo niv. {ex.niveau_assistant} — "
                         + ("dispo maintenant" if repos <= 0 else f"dispo dans {fmt_duree(repos)}"))
        travail = (ex.assistant_au_travail_s or 0) - age
        if travail > 0 and not any(b.type in ("apprenti", "assistant") and b.actif for b in self.boosts):
            self.boosts.append(Boost("apprenti", float(ex.assistant_au_travail_s), float(ex.timestamp), actif=True))
            self.journal(f"⚠ Un assistant travaille encore {fmt_duree(travail)} : choisis sa cible dans « Boosts » "
                         "(sinon il n'est pas compté).")
        autres = {k: v for k, v in ex.boosts_bruts.items() if "cooldown" not in k}
        if autres:
            self.journal(f"Boosts dans l'export (pas encore compris, envoie-les moi) : {autres}")
        if any(b.type == "potion_ouvrier" and b.actif for b in self.boosts):
            self.journal("Potion d'ouvrier gardée du dernier scan : refais « 🎯 Scan précis » si elle a changé.")
        self.rafraichir()
        self._filtre_change()
        if envoi and self.cfg.get("auto_envoi", True):
            self.envoyer(auto=True)

    # ------------------------------------------------------------------ envoi : alarmes + agenda
    def _agenda(self):
        if self.cfg.get("agenda") == "telephone":
            return agenda.AgendaTelephone(self.adb, self.cfg.get("agenda_compte", ""))
        return agenda.AgendaGoogle()

    def envoyer(self, auto=False):
        if self._envoi_en_cours:
            return
        self.recalculer()
        sorties = list(self.sorties)
        now = self.now()
        etat = load_json(STATE_FILE, {})
        alarmes_av = sorted((x["label"], x["epoch"]) for x in etat.get("alarmes", []) if x["epoch"] > now)
        events_av = sorted((x["label"], x["epoch"]) for x in etat.get("events", []) if x["epoch"] > now)
        alarmes = [s for s in sorties if s.alarme]
        events = [s for s in sorties if s.event]
        chg_alarmes = sorted((s.label, s.epoch) for s in alarmes) != alarmes_av
        chg_events = sorted((s.label, s.epoch) for s in events) != events_av
        if not (chg_alarmes or chg_events):
            self.journal("Rien n'a changé depuis le dernier envoi : rien à faire.")
            if not auto:
                QMessageBox.information(self, "Envoyer", "Alarmes et events déjà à jour.")
            return
        if events and self.cfg.get("agenda") == "google" and not agenda.AgendaGoogle.pret():
            self.journal("⚠ Google Agenda pas connecté (⚙ → Connecter Google Agenda) : events ignorés.")
            events, chg_events = [], False
        if not auto:
            avert = ""
            if any(b.inconnu for b in self.boosts):
                avert += "\n⚠ Icône de boost inconnue (non comptée)."
            if any(b.actif and b.type in ("apprenti", "assistant") and not b.cible for b in self.boosts):
                avert += "\n⚠ Apprenti/assistant sans cible (non compté)."
            lignes = "\n".join(f"  {'⏰' if s.alarme else '  '}{'📅' if s.event else '  '} {hm(s.epoch)}  {s.label}" for s in sorties)
            msg = f"⏰ = alarme Horloge, 📅 = event Google Agenda\n\n{lignes or '  (rien)'}\n"
            if chg_alarmes and self.chk_suppr.isChecked():
                msg += "\nLes anciennes alarmes « CoC: » sont supprimées avant (l'Horloge s'ouvre quelques secondes)."
            if not chg_alarmes:
                msg += "\nAlarmes déjà à jour : le téléphone n'est pas touché."
            msg += avert
            if QMessageBox.question(self, "Envoyer", msg) != QMessageBox.StandardButton.Yes:
                return
        deja = bool(etat.get("alarmes")) or not etat.get("alarmes_suivies", False)
        adb, cfg, suppr = self.adb, dict(self.cfg), self.chk_suppr.isChecked() and deja
        ag = self._agenda() if (chg_events and (events or events_av)) else None
        besoin_tel = chg_alarmes or isinstance(ag, agenda.AgendaTelephone)
        if besoin_tel and not adb.serial:
            self.journal("⚠ Téléphone pas branché : alarmes pas envoyées.")
            if not ag or isinstance(ag, agenda.AgendaTelephone):
                return
            chg_alarmes = False

        def travail():
            log, faites, evs = [], None, None
            if chg_alarmes:
                if suppr:
                    try:
                        n = core.supprimer_alarmes_coc(adb, log.append)
                        log.append(f"{n} ancienne(s) alarme(s) CoC supprimée(s)")
                    except AdbError as e:
                        log.append(f"⚠ suppression des anciennes alarmes arrêtée : {e}")
                hms = adb.local_hm([s.epoch for s in alarmes])
                faites = []
                for s, (h, m) in zip(alarmes, hms):
                    adb.set_alarm(h, m, s.label)
                    faites.append({"epoch": s.epoch, "label": s.label})
                    log.append(f"⏰ {h:02d}:{m:02d}  {s.label}")
                    time.sleep(0.8)
                if cfg.get("relancer_coc"):
                    time.sleep(0.5)
                    try:
                        adb.relaunch_coc()
                    except AdbError:
                        pass
            if ag is not None:
                try:
                    n = ag.supprimer_anciens(time.time() - 60)
                    if n:
                        log.append(f"{n} ancien(s) event(s) supprimé(s)")
                    evs = []
                    for s in events:
                        ag.creer(s.label, s.epoch, 10, "Fin d'amélioration Clash of Clans\n" + "\n".join(s.noms)
                                 + f"\n\n{agenda.MARQUE}", rappels=(int(cfg.get("rappel_avant_min", 10)), 0))
                        evs.append({"epoch": s.epoch, "label": s.label})
                        log.append(f"📅 {datetime.fromtimestamp(s.epoch):%a %d %H:%M}  {s.label}")
                except Exception as e:
                    log.append(f"⚠ Agenda : {e}")
                    evs = None
            return faites, evs, log

        def ok(r):
            faites, evs, log = r
            self._envoi_en_cours = False
            for l in log:
                self.journal(l)
            etat = load_json(STATE_FILE, {})
            if faites is not None:
                etat["alarmes"] = faites
                etat["alarmes_suivies"] = True
            if evs is not None:
                etat["events"] = evs
            save_json(STATE_FILE, etat)
            self.journal("Envoi fini.")

        self._envoi_en_cours = True
        self.lancer(travail, (), ok, "Envoi (alarmes + agenda)")

    def nettoyer(self):
        if QMessageBox.question(self, "Supprimer", "Supprimer toutes les alarmes « CoC: » de l'Horloge ?\n"
                                "(tes autres alarmes ne sont pas touchées)") != QMessageBox.StandardButton.Yes:
            return
        adb, rel = self.adb, self.cfg.get("relancer_coc")

        def travail():
            log = []
            try:
                log.append(f"{core.supprimer_alarmes_coc(adb, log.append)} alarme(s) CoC supprimée(s)")
            finally:
                if rel:
                    try:
                        adb.relaunch_coc()
                    except AdbError:
                        pass
            return log

        self.lancer(travail, (), lambda log: [self.journal(l) for l in log], "Suppression des alarmes CoC")

    def voir_alarmes(self):
        try:
            self.adb.show_alarms()
        except AdbError as e:
            self._erreur(str(e))

    def closeEvent(self, ev):
        if self.tray is not None and self.chk_auto.isChecked() and not self._quitter:
            # mode automatique : on reste dans la barre des tâches
            ev.ignore()
            self.hide()
            if not self._prevenu_fond:
                self._prevenu_fond = True
                self.tray.showMessage("CoC Alarmes continue", "Le mode automatique tourne en fond. "
                                      "Clic droit sur l'icône → Quitter pour arrêter.", theme.icone_appli(), 6000)
            return
        ev.accept()
        self._nettoyer_et_quitter()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    theme.charger_polices()
    app.setStyleSheet(theme.feuille())
    app.setApplicationName("CoC Alarmes")
    app.setDesktopFileName("coc-alarmes")
    # une seule appli à la fois : si elle tourne déjà (même cachée), on la rouvre
    from PyQt6.QtNetwork import QLocalServer, QLocalSocket
    nom = "coc-alarmes-" + str(Path.home().name)
    sock = QLocalSocket()
    sock.connectToServer(nom)
    if sock.waitForConnected(400):
        sock.write(b"montre"); sock.flush(); sock.waitForBytesWritten(400)
        return
    QLocalServer.removeServer(nom)
    serveur = QLocalServer()
    serveur.listen(nom)
    app.setQuitOnLastWindowClosed(False)
    w = Fenetre()
    serveur.newConnection.connect(lambda: (serveur.nextPendingConnection(), w._montrer()))
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
