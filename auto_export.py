"""Copie l'export du village tout seul : profil → Paramètres → Paramètres supplémentaires → Copier,
puis ouvre le menu des ouvriers pour lire les potions actives (bulles à côté du menu).

Deux chemins :
  - lent (1re fois, ou si l'écran a changé) : lecture OCR complète à chaque étape ; il apprend
    où sont les boutons et l'enregistre ;
  - rapide : tape directement aux positions apprises, et vérifie juste le mot du bouton
    (lecture d'une petite case, ~10 ms) avant chaque appui important.
On ne tape QUE sur un texte attendu, jamais « Supprimer »."""
from __future__ import annotations

import difflib
import re
import time
import unicodedata

from core import Adb, AdbError, COC_PKG, ocr, lire_ligne, analyser, load_json, save_json, STATE_FILE
from export import est_export
from presse_papier import PontPressePapier


class Occupe(AdbError):
    pass


class Recalibrer(AdbError):
    pass


INTERDIT = ("supprimer", "acheter", "gemmes", "valider", "confirmer")


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    return " ".join(t.split())


def _ressemble(lu: str, attendu: str) -> bool:
    n = _norm(lu)
    return attendu in n or difflib.SequenceMatcher(None, n, attendu).ratio() >= 0.6


def _boite(m, W, H):
    return [m.x1 / W, m.y1 / H, m.x2 / W, m.y2 / H]


class ExportAuto:
    def __init__(self, adb: Adb, pont: PontPressePapier, log=print, lire_boosts=True, cfg=None):
        self.adb, self.pont, self.log = adb, pont, log
        self.lire_boosts = lire_boosts
        self.cfg = cfg or {}
        self.temps = []          # (étape, secondes) pour mesurer
        self._t0 = time.time()

    # ------------------------------------------------------------------ outils
    def _marque(self, etape):
        t = time.time()
        self.temps.append((etape, t - self._t0))
        self._t0 = t

    def _cap(self):
        img, _ = self.adb.screencap()
        return img

    def _ecran(self):
        img = self._cap()
        return img, ocr(img)

    def _trouver(self, mots, *cibles, exact=False):
        for m in mots:
            n = _norm(m.texte)
            for c in cibles:
                if (n == c) if exact else (c in n):
                    return m
        return None

    def _taper_xy(self, x, y, attente):
        self.adb.shell(f"input tap {int(x)} {int(y)}")
        time.sleep(attente)

    def _taper(self, m, attente=1.3):
        if any(x in _norm(m.texte) for x in INTERDIT):
            raise AdbError(f"refus de taper sur « {m.texte} »")
        self._taper_xy((m.x1 + m.x2) // 2, (m.y1 + m.y2) // 2, attente)

    def _attendre(self, *cibles, essais=4):
        for _ in range(essais):
            img, mots = self._ecran()
            m = self._trouver(mots, *cibles)
            if m:
                return img, mots, m
            time.sleep(0.5)
        return None, None, None

    def _lit(self, img, boite, attendu) -> bool:
        """Lit juste la petite case d'un bouton et vérifie le mot."""
        W, H = img.size
        x1, y1, x2, y2 = boite[0] * W, boite[1] * H, boite[2] * W, boite[3] * H
        mx, my = (x2 - x1) * 0.15, (y2 - y1) * 0.3
        txt, _ = lire_ligne(img, (x1 - mx, y1 - my, x2 + mx, y2 + my))
        return _ressemble(txt, attendu)

    def coc_au_premier_plan(self) -> bool:
        out = self.adb.shell("dumpsys window | grep -m1 'mCurrentFocus=Window'")
        return COC_PKG in out

    def _croix(self, W, H, attente=0.6):
        self._taper_xy(W * 0.806, H * 0.079, attente)   # croix rouge du menu profil

    def _attendre_json(self, delai=4.0):
        fin = time.time() + delai
        while time.time() < fin:
            if self.pont._attente.wait(0.1) and self.pont.dernier and est_export(self.pont.dernier):
                return self.pont.dernier
        return None

    # ------------------------------------------------------------------ entrée
    def lancer(self, auto: bool = False):
        """Renvoie (texte_export, scan_du_menu_ouvriers ou None)."""
        if not self.coc_au_premier_plan():
            raise AdbError("Clash of Clans n'est pas ouvert à l'écran")
        if auto:
            ina = self.adb.inactivite_s()
            if ina is not None and ina < 90:
                raise Occupe(f"tu utilises le téléphone (dernier toucher il y a {ina:.0f} s)")
        if not self.pont.actif:
            self.pont.demarrer()
        self._marque("début")
        calib = load_json(STATE_FILE, {}).get("calib_export")
        texte, scan, village_ok = None, None, False
        if calib and self.lire_boosts and calib.get("ouvriers"):
            # potions d'abord : on part du village, on y revient -> pas besoin de le revérifier après
            try:
                scan = self._menu_ouvriers()
                village_ok = True
            except AdbError as e:
                self.log(f"potions pas lues : {e}")
        if calib:
            try:
                texte = self._rapide(calib, village_ok)
            except Recalibrer as e:
                self.log(f"chemin rapide abandonné ({e}) : je réapprends les positions")
        if texte is None:
            texte = self._lent()
            if self.lire_boosts and scan is None:
                try:
                    scan = self._menu_ouvriers()
                except AdbError as e:
                    self.log(f"potions pas lues : {e}")
        return texte, scan

    # ------------------------------------------------------------------ rapide
    def _rapide(self, c, village_ok=False) -> str:
        if village_ok:
            W, H = c["res"]
        else:
            img = self._cap(); W, H = img.size
            if c.get("res") != [W, H]:
                raise Recalibrer("taille d'écran changée")
            if not self._lit(img, c["combat"], "combat"):
                raise Recalibrer("écran du village pas reconnu")
            self._marque("vérif village")
        self._taper_xy(W * 0.062, H * 0.111, 0.9)                   # profil
        img = self._cap()
        if not self._lit(img, c["onglet"], "parametres"):
            self.adb.shell("input keyevent KEYCODE_BACK")
            raise Recalibrer("menu profil pas ouvert")
        self._marque("profil")
        x, y = (c["onglet"][0] + c["onglet"][2]) / 2 * W, (c["onglet"][1] + c["onglet"][3]) / 2 * H
        self._taper_xy(x, y, 0.7)                                    # onglet Paramètres
        x, y = (c["plus"][0] + c["plus"][2]) / 2 * W, (c["plus"][1] + c["plus"][3]) / 2 * H
        self._taper_xy(x, y, 0.9)                                    # Paramètres supplémentaires
        sx = int(W * 0.34)
        for _ in range(4):                                           # tout en bas
            self.adb.shell(f"input swipe {sx} {int(H * 0.8)} {sx} {int(H * 0.25)} 200")
        time.sleep(0.6)
        self._marque("menus + défilement")
        img = self._cap()
        if not (self._lit(img, c["copier"], "copier") and self._lit(img, c["ligne"], "exporter les donnees")):
            self._croix(W, H)
            raise Recalibrer("bouton Copier pas à sa place")
        self._marque("vérif Copier")
        self.pont.dernier = None
        self.pont._attente.clear()
        x, y = (c["copier"][0] + c["copier"][2]) / 2 * W, (c["copier"][1] + c["copier"][3]) / 2 * H
        self._taper_xy(x, y, 0.0)
        texte = self._attendre_json()
        self._marque("copie reçue")
        self._croix(W, H)
        if not texte:
            raise AdbError("export copié mais pas reçu sur le PC")
        return texte

    # ------------------------------------------------------------------ lent (et apprentissage)
    def _lent(self) -> str:
        calib = {}
        img, mots = self._ecran()
        W, H = img.size
        calib["res"] = [W, H]
        rech = self._trouver(mots, "recharger", "reconnecter", "reessayer")
        if rech and not self._trouver(mots, "combat"):
            self._taper(rech, 10)
            img, mots = self._ecran()
        combat = self._trouver(mots, "combat")
        if not (combat and self._trouver(mots, "construire", "magasin")):
            raise AdbError("pas sur l'écran du village (attaque ou menu ouvert ?)")
        calib["combat"] = _boite(combat, W, H)
        cpt = [m for m in mots if re.fullmatch(r"\d{1,2}\s*/\s*\d{1,2}", m.texte.strip()) and m.x1 > W * 0.7 and m.y1 > H * 0.5]
        if cpt:
            calib["ouvriers"] = _boite(min(cpt, key=lambda m: m.x1), W, H)
        self._marque("village (lent)")
        self._taper_xy(W * 0.062, H * 0.111, 1.2)
        img, mots, onglet = self._attendre("parametres")
        if not onglet:
            raise AdbError("menu profil pas ouvert")
        onglet = next((m for m in mots if _norm(m.texte) == "parametres" and m.y2 < H * 0.15), onglet)
        calib["onglet"] = _boite(onglet, W, H)
        self._taper(onglet, 0.8)
        img, mots, plus = self._attendre("supplementaires")
        if not plus:
            self._croix(W, H)
            raise AdbError("bouton « Paramètres supplémentaires » pas trouvé")
        calib["plus"] = _boite(plus, W, H)
        self._taper(plus, 1.0)
        sx = int(W * 0.34)
        for _ in range(4):
            self.adb.shell(f"input swipe {sx} {int(H * 0.8)} {sx} {int(H * 0.25)} 200")
        time.sleep(0.7)
        copier = ligne = None
        for _ in range(5):
            img, mots = self._ecran()
            ligne = self._trouver(mots, "exporter les donnees du village", "format json")
            if ligne:
                cands = [m for m in mots if m.x1 > ligne.x2 and len(_norm(m.texte)) <= 8
                         and abs((m.y1 + m.y2) / 2 - (ligne.y1 + ligne.y2) / 2) < 1.5 * ligne.h
                         and difflib.SequenceMatcher(None, _norm(m.texte), "copier").ratio() >= 0.6]
                if cands:
                    copier = cands[0]
                    break
            self.adb.shell(f"input swipe {sx} {int(H * 0.8)} {sx} {int(H * 0.3)} 300")
            time.sleep(0.6)
        if not copier:
            self._croix(W, H)
            raise AdbError("bouton « Copier » de l'export pas trouvé")
        calib["copier"] = _boite(copier, W, H)
        calib["ligne"] = _boite(ligne, W, H)
        self.pont.dernier = None
        self.pont._attente.clear()
        self._taper(copier, 0.0)
        texte = self._attendre_json()
        self._croix(W, H)
        if not texte:
            raise AdbError("export copié mais pas reçu sur le PC")
        st = load_json(STATE_FILE, {})
        st["calib_export"] = calib
        save_json(STATE_FILE, st)
        self._marque("export (lent, positions apprises)")
        return texte

    # ------------------------------------------------------------------ potions
    def _menu_ouvriers(self):
        """Ouvre le menu des ouvriers, lit les bulles de boost (potions), referme."""
        c = load_json(STATE_FILE, {}).get("calib_export") or {}
        img = self._cap(); W, H = img.size
        if not self._lit(img, c.get("combat", [0, 0, 0, 0]), "combat"):
            time.sleep(0.6)
            img = self._cap()
            if not self._lit(img, c.get("combat", [0, 0, 0, 0]), "combat"):
                raise AdbError("pas revenu sur le village")
        b = c.get("ouvriers")
        if not b:
            raise AdbError("compteur d'ouvriers pas trouvé")
        self._taper_xy((b[0] + b[2]) / 2 * W, (b[1] + b[3]) / 2 * H, 1.0)
        img, t = self.adb.screencap()
        res = analyser(img, t, self.cfg)
        if res.ameliorations or res.boosts:
            self.adb.shell("input keyevent KEYCODE_BACK")   # ferme le menu des ouvriers
            time.sleep(0.6)
        self._marque("menu des ouvriers (potions)")
        if not res.ameliorations:
            raise AdbError("menu des ouvriers pas ouvert")
        return res
