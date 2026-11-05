"""Frise verticale : quand chaque amélioration finit, la nuit en grisé, alarme / agenda / rien."""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import QPainter, QPen, QFontMetrics
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QLabel, QSizePolicy

import theme

COULEURS = {k: theme.qcolor(v) for k, v in theme.CATEGORIE.items()}


class _Toile(QWidget):
    def __init__(self):
        super().__init__()
        self.points = []          # (epoch, nom, categorie, sortie) ; sortie : "A", "E", "AE", "" ou "filtré"
        self.eveil = ((7, 0), (23, 0))
        self.horizon_h = 48
        self.setMinimumSize(240, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._ic = {"A": theme.pixmap("alarme", theme.VERT, 14), "E": theme.pixmap("agenda", theme.ORANGE, 14)}

    def _eveille(self, d: datetime) -> bool:
        m = d.hour * 60 + d.minute
        a = self.eveil[0][0] * 60 + self.eveil[0][1]
        b = self.eveil[1][0] * 60 + self.eveil[1][1]
        return a <= m < b if a < b else (m >= a or m < b)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        haut, bas, gauche = 12, 12, 66
        now = time.time()
        fin = now + self.horizon_h * 3600

        def y_de(t):
            return haut + (t - now) / (fin - now) * (H - haut - bas)

        # nuits
        nuit = theme.qcolor(theme.ORANGE, 22)
        t = now
        while t < fin:
            if not self._eveille(datetime.fromtimestamp(t)):
                p.fillRect(QRectF(gauche, y_de(t), W - gauche, y_de(min(fin, t + 600)) - y_de(t) + 0.6), nuit)
            t += 600
        # graduations
        p.setFont(theme.police(11, gras=True))
        pas_h = 3 if self.horizon_h <= 24 else (6 if self.horizon_h <= 72 else 24)
        d = datetime.fromtimestamp(now).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        grille = theme.qcolor(theme.BORD)
        while d.timestamp() < fin:
            if d.hour % pas_h == 0:
                y = y_de(d.timestamp())
                p.setPen(QPen(grille, 1))
                p.drawLine(QPointF(gauche, y), QPointF(W, y))
                debut_jour = d.hour == 0
                p.setPen(theme.qcolor(theme.OR if debut_jour else theme.TEXTE_3))
                lab = f"{['lun', 'mar', 'mer', 'jeu', 'ven', 'sam', 'dim'][d.weekday()]} {d.day}" if debut_jour else d.strftime("%Hh")
                p.drawText(QRectF(0, y - 8, gauche - 8, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, lab)
            d += timedelta(hours=1)
        # maintenant
        p.setPen(QPen(theme.qcolor(theme.ROUGE), 2))
        p.drawLine(QPointF(gauche, haut), QPointF(W, haut))
        p.drawText(QRectF(0, haut - 8, gauche - 8, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "maint.")
        # points + étiquettes, décalées pour ne pas se chevaucher
        f = theme.police(12, gras=True)
        p.setFont(f)
        fm = QFontMetrics(f)
        lh = fm.height() + 2
        dernier = -1e9
        x_axe = gauche + 12
        for t, nom, cat, sortie in sorted(self.points):
            if not (now <= t <= fin):
                continue
            y = y_de(t)
            ye = max(y, dernier + lh)
            dernier = ye
            col = COULEURS.get(cat, theme.qcolor(theme.TEXTE_2))
            eteint = sortie in ("", "filtré")
            if eteint:
                col.setAlpha(90)
            p.setPen(QPen(col, 1.5))
            p.drawLine(QPointF(x_axe, y), QPointF(x_axe + 10, ye))
            p.setBrush(col)
            p.drawEllipse(QPointF(x_axe, y), 4.5, 4.5)
            x = x_axe + 16
            p.setPen(theme.qcolor(theme.TEXTE_3 if eteint else theme.TEXTE))
            heure = datetime.fromtimestamp(t).strftime("%H:%M")
            p.drawText(QPointF(x, ye + fm.ascent() / 2 - 1), heure)
            x += fm.horizontalAdvance("00:00") + 6
            for k in ("A", "E"):
                if k in sortie and sortie != "filtré":
                    p.drawPixmap(int(x), int(ye - 7), self._ic[k])
                    x += 17
            p.setPen(theme.qcolor(theme.TEXTE_3 if eteint else theme.TEXTE_2))
            p.drawText(QPointF(x + 2, ye + fm.ascent() / 2 - 1), fm.elidedText(nom, Qt.TextElideMode.ElideRight, int(W - x - 6)))
        p.end()


class Chronologie(QWidget):
    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        h = QHBoxLayout()
        lv = QLabel("Voir les"); lv.setObjectName("petit")
        h.addWidget(lv)
        self.cb = QComboBox()
        for txt, val in (("prochaines 24 h", 24), ("prochaines 48 h", 48), ("3 prochains jours", 72), ("7 prochains jours", 168)):
            self.cb.addItem(txt, val)
        self.cb.setCurrentIndex(1)
        self.cb.currentIndexChanged.connect(self._horizon)
        h.addWidget(self.cb)
        h.addStretch(1)
        ln = QLabel("orange = la nuit"); ln.setObjectName("petit")
        h.addWidget(ln)
        v.addLayout(h)
        self.toile = _Toile()
        v.addWidget(self.toile, 1)

    def _horizon(self):
        self.toile.horizon_h = self.cb.currentData()
        self.toile.update()

    def maj(self, points, eveil):
        self.toile.points = points
        self.toile.eveil = eveil
        self.toile.update()
