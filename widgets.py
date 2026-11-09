"""Widgets peints à la main, style Clash of Clans : texte à contour, gros boutons 3D, interrupteur, pastilles."""
from __future__ import annotations

from PyQt6.QtCore import Qt, QRectF, QPointF, QSize, QPropertyAnimation, QEasingCurve, pyqtProperty
from PyQt6.QtGui import (QPainter, QPainterPath, QPen, QColor, QLinearGradient, QFontMetricsF, QBrush)
from PyQt6.QtWidgets import QWidget, QPushButton, QAbstractButton, QSizePolicy

import theme


def chemin_texte(texte: str, police, x: float, y_base: float) -> QPainterPath:
    p = QPainterPath()
    p.addText(QPointF(x, y_base), police, texte)
    return p


def peindre_texte_coc(p: QPainter, rect: QRectF, texte: str, police, remplissage: QColor,
                      contour: float = 3.0, ombre: float = 2.0, alignement=Qt.AlignmentFlag.AlignLeft):
    """Texte blanc/or avec contour noir épais et petite ombre, comme dans le jeu."""
    fm = QFontMetricsF(police)
    largeur = fm.horizontalAdvance(texte)
    if alignement & Qt.AlignmentFlag.AlignHCenter:
        x = rect.x() + (rect.width() - largeur) / 2
    elif alignement & Qt.AlignmentFlag.AlignRight:
        x = rect.right() - largeur - contour
    else:
        x = rect.x() + contour
    y = rect.y() + (rect.height() + fm.capHeight()) / 2
    chemin = chemin_texte(texte, police, x, y)
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if ombre:
        p.translate(0, ombre)
        p.strokePath(chemin, QPen(QColor(0, 0, 0, 200), contour * 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                  Qt.PenJoinStyle.RoundJoin))
        p.translate(0, -ombre)
    p.strokePath(chemin, QPen(QColor("#050505"), contour * 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                              Qt.PenJoinStyle.RoundJoin))
    p.fillPath(chemin, remplissage)
    p.restore()


class TexteCoC(QWidget):
    """Titre façon Clash of Clans (police ronde, contour noir)."""

    def __init__(self, texte="", taille=24, couleur=theme.TEXTE, contour=3.0, alignement=Qt.AlignmentFlag.AlignLeft, parent=None):
        super().__init__(parent)
        self._texte, self._taille, self._couleur, self._contour = texte, taille, QColor(couleur), contour
        self._align = alignement
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def police(self):
        return theme.police(self._taille, titre=True)

    def setText(self, t):
        if t != self._texte:
            self._texte = t
            self.updateGeometry()
            self.update()

    def text(self):
        return self._texte

    def setCouleur(self, c):
        self._couleur = QColor(c)
        self.update()

    def sizeHint(self):
        fm = QFontMetricsF(self.police())
        return QSize(int(fm.horizontalAdvance(self._texte) + self._contour * 4 + 4), int(fm.height() + self._contour * 2 + 4))

    def minimumSizeHint(self):
        # les titres longs peuvent rétrécir, les courts jamais (sinon coupés)
        sh = self.sizeHint()
        return QSize(min(sh.width(), 260), sh.height())

    def paintEvent(self, ev):
        p = QPainter(self)
        peindre_texte_coc(p, QRectF(self.rect()), self._texte, self.police(), self._couleur, self._contour,
                          max(1.0, self._taille / 12), self._align)
        p.end()


VARIANTES = {
    #          face haut   face bas    rebord      contour
    "vert": ("#8EE061", "#55B33A", "#2F7A1F", "#173F0E"),
    "or": ("#FFE07A", "#FFB727", "#B97A0C", "#4A3104"),
    "gris": ("#3A3A48", "#262632", "#15151C", "#05050A"),
    "rouge": ("#FF8A80", "#E2453C", "#8E1F1A", "#3A0806"),
}


class BoutonCoC(QPushButton):
    """Gros bouton 3D (face dégradée, rebord sombre, texte à contour)."""

    def __init__(self, texte="", variante="vert", icone=None, taille=18, parent=None):
        super().__init__(texte, parent)
        self.variante = variante
        self._icone = icone
        self._taille = taille
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")
        self._survol = False

    def setVariante(self, v):
        if v != self.variante:
            self.variante = v
            self.update()

    def setIcone(self, nom):
        self._icone = nom
        self.update()

    def enterEvent(self, e):
        self._survol = True; self.update(); super().enterEvent(e)

    def leaveEvent(self, e):
        self._survol = False; self.update(); super().leaveEvent(e)

    def sizeHint(self):
        fm = QFontMetricsF(theme.police(self._taille, titre=True))
        l = fm.horizontalAdvance(self.text()) + 48 + (30 if self._icone else 0)
        return QSize(int(max(140, l)), int(fm.height() + 30))

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        haut, bas, rebord, contour = VARIANTES["gris" if not self.isEnabled() else self.variante]
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        enfonce = 3 if self.isDown() else 0
        prof = 5
        # rebord (épaisseur 3D)
        base = QPainterPath(); base.addRoundedRect(r.adjusted(0, prof, 0, 0), 12, 12)
        p.fillPath(base, QColor(rebord))
        face_r = r.adjusted(0, enfonce, 0, -prof + enfonce)
        face = QPainterPath(); face.addRoundedRect(face_r, 12, 12)
        g = QLinearGradient(face_r.topLeft(), face_r.bottomLeft())
        c1, c2 = QColor(haut), QColor(bas)
        if self._survol and self.isEnabled():
            c1, c2 = c1.lighter(110), c2.lighter(110)
        g.setColorAt(0, c1); g.setColorAt(1, c2)
        p.fillPath(face, QBrush(g))
        # reflet en haut
        reflet = QPainterPath(); reflet.addRoundedRect(face_r.adjusted(6, 3, -6, -face_r.height() * 0.55), 8, 8)
        p.fillPath(reflet, QColor(255, 255, 255, 46))
        tout = QPainterPath(); tout.addRoundedRect(r.adjusted(0, enfonce, 0, 0), 12, 12)
        p.setPen(QPen(QColor(contour), 2)); p.drawPath(tout)
        if self.hasFocus():
            p.setPen(QPen(QColor(theme.OR), 2, Qt.PenStyle.DashLine)); p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), 13, 13)
        # contenu
        police = theme.police(self._taille, titre=True)
        fm = QFontMetricsF(police)
        lt = fm.horizontalAdvance(self.text())
        li = 26 if self._icone else 0
        x0 = face_r.x() + (face_r.width() - lt - li) / 2
        if self._icone:
            pm = theme.pixmap(self._icone, "#FFFFFF" if self.isEnabled() else theme.TEXTE_3, 20)
            ombre = theme.pixmap(self._icone, "#050505", 20)
            yi = int(face_r.center().y() - 10)
            p.drawPixmap(int(x0) + 1, yi + 2, ombre)
            p.drawPixmap(int(x0), yi, pm)
        peindre_texte_coc(p, QRectF(x0 + li, face_r.y(), lt + 12, face_r.height()), self.text(), police,
                          QColor("#FFFFFF" if self.isEnabled() else theme.TEXTE_3), 2.6, 1.6)
        p.end()


class Interrupteur(QAbstractButton):
    """Interrupteur rond (vert allumé, gris éteint), avec texte à droite."""

    def __init__(self, texte="", parent=None):
        super().__init__(parent)
        self.setText(texte)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pos = 0.0
        self._anim = QPropertyAnimation(self, b"pos_curseur", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._bouger)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def _get(self):
        return self._pos

    def _set(self, v):
        self._pos = v
        self.update()

    pos_curseur = pyqtProperty(float, _get, _set)

    def _bouger(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def setChecked(self, on):
        super().setChecked(on)
        self._pos = 1.0 if on else 0.0
        self.update()

    def sizeHint(self):
        fm = QFontMetricsF(theme.police(15, titre=True))
        return QSize(int(52 + (fm.horizontalAdvance(self.text()) + 12 if self.text() else 0)), 30)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        piste = QRectF(1, 3, 48, 24)
        fond = QColor(theme.VERT) if self.isChecked() else QColor(theme.SURFACE_3)
        p.setPen(QPen(QColor("#050505"), 2))
        p.setBrush(fond)
        p.drawRoundedRect(piste, 12, 12)
        x = piste.x() + 3 + self._pos * (piste.width() - 24)
        p.setBrush(QColor("#FFFFFF"))
        p.setPen(QPen(QColor("#050505"), 1.5))
        p.drawEllipse(QRectF(x, piste.y() + 3, 18, 18))
        if self.hasFocus():
            p.setPen(QPen(QColor(theme.OR), 2)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(piste.adjusted(-2, -2, 2, 2), 14, 14)
        if self.text():
            peindre_texte_coc(p, QRectF(58, 0, self.width() - 58, self.height()), self.text(),
                              theme.police(15, titre=True), QColor(theme.TEXTE), 2.0, 1.0)
        p.end()

    def hitButton(self, pos):
        return self.rect().contains(pos)


class PastilleIcone(QWidget):
    """Rond de couleur avec l'icône de la catégorie."""

    def __init__(self, icone: str, couleur: str, taille=40, eteint=False, parent=None):
        super().__init__(parent)
        self.icone, self.couleur, self.taille, self.eteint = icone, couleur, taille, eteint
        self.setFixedSize(taille, taille)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(theme.TEXTE_3 if self.eteint else self.couleur)
        fond = QColor(c); fond.setAlpha(40)
        p.setPen(QPen(c, 1.5))
        p.setBrush(fond)
        p.drawEllipse(QRectF(1, 1, self.taille - 2, self.taille - 2))
        t = int(self.taille * 0.55)
        p.drawPixmap((self.taille - t) // 2, (self.taille - t) // 2, theme.pixmap(self.icone, c.name(), t))
        p.end()
