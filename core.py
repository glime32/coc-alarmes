"""Coeur : ADB, OCR, lecture des timers, suivi précis, calcul des boosts, conseils, alarmes."""
from __future__ import annotations

import json
import math
import re
import shlex
import struct
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image

DATA_DIR = Path.home() / ".local/share/coc-alarmes"
ICON_DIR = DATA_DIR / "icones"
BASE_ICON_DIR = Path(__file__).resolve().parent / "icones_base"
CONFIG_FILE = DATA_DIR / "config.json"
STATE_FILE = DATA_DIR / "etat.json"
DERNIERE_CAPTURE = DATA_DIR / "derniere_capture.png"
COC_PKG = "com.supercell.clashofclans"

# --------------------------------------------------------------------------
# Règles (wiki Clash of Clans, nov. 2026)
#  - Potion d'ouvrier : ouvriers x10 pendant 1 h (+9). Pas sur l'apprenti.
#  - Builder Bite (snack) : ouvriers x2 pendant 1 h (+1). Pas sur l'apprenti.
#    Depuis la maj du 26 fév. : ne se cumule plus avec la potion.
#  - Potion de recherche : labo x24 1 h (+23). Pas sur l'assistant.
#  - Study Soup (snack) : labo + animalerie x4 1 h (+3). Cumul additif.
#  - Potion d'animaux : animalerie x24 1 h (+23).
#  - Tour de l'horloge (+ potion) : base des ouvriers x10 (+9).
#  - Apprenti / Assistant : niveau N = +N, sur UNE amélioration, 1 h, puis repos
#    (journée de travail de 23 h commune à tous les assistants).
#  Le timer affiché = travail restant ; il défile plus vite quand c'est boosté
#  (le « Scan précis » le vérifie en mesurant la vitesse réelle).
# --------------------------------------------------------------------------
CATEGORIES = ["Ouvriers", "Labo", "Animalerie", "Forge", "Assistants", "Base ouvriers"]
VILLAGE_PRINCIPAL = ["Ouvriers", "Labo", "Animalerie", "Forge", "Assistants"]

BOOST_TYPES = {
    # clé: (libellé, bonus, catégories touchées, groupe non cumulable)
    "potion_ouvrier": ("Potion d'ouvrier (x10)", 9, ["Ouvriers"], "ouvrier"),
    "snack_ouvrier": ("Builder Bite / snack ouvrier (x2)", 1, ["Ouvriers"], "ouvrier"),
    "potion_recherche": ("Potion de recherche (x24)", 23, ["Labo"], None),
    "soupe_etude": ("Study Soup / snack labo (x4)", 3, ["Labo", "Animalerie"], None),
    "potion_animaux": ("Potion d'animaux (x24)", 23, ["Animalerie"], None),
    "horloge": ("Tour de l'horloge (x10)", 9, ["Base ouvriers"], None),
    "apprenti": ("Apprenti ouvrier (1 amélioration)", None, [], None),
    "assistant": ("Assistant de labo (1 recherche)", None, [], None),
    "ignorer": ("Ignorer", 0, [], None),
}

DEFAULT_CONFIG = {
    "serial": "",
    "niveau_apprenti": 1,
    "niveau_assistant": 1,
    "potion_snack_cumul": False,
    "fenetre_heures": 24,
    "avance_minutes": 0,
    "relancer_coc": True,
    "seuil_icone_hist": 0.6,
    "eveil_debut": "07:00",
    "eveil_fin": "23:00",
    "potions_dispo": 1,
    "apprenti_dispo": True,
    "apprenti_dispo_dans_min": 0,
    "horizon_conseil_h": 96,
    "mode_jour": "alarme",          # alarme | alarme+event | event
    "mode_nuit": "event",           # event | alarme+event | alarme | rien
    "agenda": "google",             # google | telephone
    "agenda_compte": "",
    "horizon_event_h": 168,
    "auto_envoi": True,
    "rappel_avant_min": 10,
    "categories_actives": ["Ouvriers", "Labo", "Animalerie", "Forge", "Assistants", "Base ouvriers"],
    "auto_export_min": 120,
    "lire_potions": True,
}


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(path)


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(load_json(CONFIG_FILE, {}))
    return cfg


# --------------------------------------------------------------------------
# ADB
# --------------------------------------------------------------------------
class AdbError(RuntimeError):
    pass


class Adb:
    def __init__(self, serial: str = ""):
        self.serial = serial
        self.decalage = 0.0      # heure téléphone - heure PC (s)

    def _base(self):
        return ["adb"] + (["-s", self.serial] if self.serial else [])

    def run(self, args, binary=False, timeout=20):
        try:
            r = subprocess.run(self._base() + args, capture_output=True, timeout=timeout)
        except FileNotFoundError:
            raise AdbError("adb introuvable")
        except subprocess.TimeoutExpired:
            raise AdbError("adb ne répond pas (timeout)")
        if r.returncode != 0:
            raise AdbError((r.stderr or r.stdout).decode(errors="replace").strip() or "erreur adb")
        return r.stdout if binary else r.stdout.decode(errors="replace")

    def shell(self, cmd: str, timeout=20) -> str:
        return self.run(["shell", cmd], timeout=timeout)

    def devices(self):
        out = subprocess.run(["adb", "devices"], capture_output=True, text=True).stdout
        return [l.split()[0] for l in out.splitlines()[1:] if l.strip().endswith("device")]

    def synchroniser(self) -> float:
        """Mesure le décalage horloge téléphone/PC (précis à ~0,1 s)."""
        best = None
        for _ in range(3):
            t0 = time.time()
            s = self.shell("date +%s.%N").strip()
            t1 = time.time()
            try:
                ph = float(s)
            except ValueError:
                ph = float(self.shell("date +%s").strip()) + 0.5
            rtt = t1 - t0
            if best is None or rtt < best[0]:
                best = (rtt, ph - (t0 + t1) / 2)
        self.decalage = best[1]
        return self.decalage

    def now(self) -> float:
        return time.time() + self.decalage

    def screencap(self) -> tuple[Image.Image, float]:
        """Capture brute (rapide). Renvoie (image, heure téléphone de la capture)."""
        t0 = time.time()
        raw = self.run(["exec-out", "screencap"], binary=True, timeout=30)
        if len(raw) < 16:
            raise AdbError("capture vide")
        w, h, fmt = struct.unpack("<3I", raw[:12])
        n = w * h * 4
        if fmt != 1 or len(raw) < n + 12:
            # format inattendu : on repasse en PNG
            import io
            png = self.run(["exec-out", "screencap", "-p"], binary=True, timeout=30)
            return Image.open(io.BytesIO(png)).convert("RGB"), t0 + 0.15 + self.decalage
        data = raw[len(raw) - n:]
        img = Image.frombuffer("RGBA", (w, h), data, "raw", "RGBA", 0, 1).convert("RGB")
        return img, t0 + 0.15 + self.decalage

    def inactivite_s(self) -> float | None:
        """Secondes depuis le dernier toucher sur le téléphone (None si inconnu)."""
        out = self.shell("dumpsys power")
        m_now = re.search(r"mLastWakeTime=(\d+) \((\d+) ms ago\)", out)
        m_act = re.search(r"mLastUserActivityTime\(excludingAttention\)=(\d+)", out) or \
            re.search(r"mLastUserActivityTime=(\d+)", out)
        if not (m_now and m_act):
            return None
        maintenant = int(m_now.group(1)) + int(m_now.group(2))
        return max(0.0, (maintenant - int(m_act.group(1))) / 1000)

    def local_hm(self, epochs: list[int]) -> list[tuple[int, int]]:
        """Heure locale du téléphone (gère fuseau + heure d'été)."""
        if not epochs:
            return []
        cmd = "; ".join(f"date -d @{int(e)} +%H:%M" for e in epochs)
        res = []
        for l in self.shell(cmd).split():
            h, m = l.split(":")
            res.append((int(h), int(m)))
        if len(res) != len(epochs):
            raise AdbError("conversion d'heure ratée sur le téléphone")
        return res

    def set_alarm(self, hour: int, minute: int, label: str):
        cmd = (
            "am start -a android.intent.action.SET_ALARM"
            f" --ei android.intent.extra.alarm.HOUR {hour}"
            f" --ei android.intent.extra.alarm.MINUTES {minute}"
            f" --es android.intent.extra.alarm.MESSAGE {shlex.quote(label)}"
            " --ez android.intent.extra.alarm.SKIP_UI true"
        )
        out = self.shell(cmd)
        if "Error" in out:
            raise AdbError(out.strip())
        return out

    def show_alarms(self):
        return self.shell("am start -a android.intent.action.SHOW_ALARMS")

    def relaunch_coc(self):
        return self.shell(f"monkey -p {COC_PKG} -c android.intent.category.LAUNCHER 1")


# --------------------------------------------------------------------------
# Lecture des durées affichées
# --------------------------------------------------------------------------
UNIT_S = {"j": 86400, "h": 3600, "min": 60, "s": 1}
# précision d'un affichage selon sa plus grande unité ("1j" = 1j 0h, "4h" = 4h 0min)
PAS = {"j": 3600, "h": 60, "min": 1, "s": 1}
_FIX = str.maketrans({"o": "0", "i": "1", "l": "1", "u": "1", "ü": "1", "|": "1", "!": "1",
                      "s": "5", "b": "8", "g": "6", "z": "2", "t": "1"})
_N = r"([0-9oiluü|!sbgzt]{1,2})"
_FORMES = [re.compile(p) for p in (
    _N + r"(j)(?:" + _N + r"(h))?",
    _N + r"(h)(?:" + _N + r"(min))?",
    _N + r"(min)(?:" + _N + r"(s))?",
    _N + r"(s)()()",
)]


@dataclass
class Duree:
    secondes: int      # valeur affichée
    pas: int           # résolution de l'affichage (s)
    texte: str
    corrige: bool = False


def parse_duree(texte: str) -> Duree | None:
    s = texte.lower()
    s = s.replace("mín", "min").replace("mın", "min").replace("mim", "min").replace("mn", "min")
    s = re.sub(r"[^0-9a-zü|!]", "", s)
    if not s:
        return None
    m = None
    for forme in _FORMES:
        m = forme.fullmatch(s)
        if m:
            break
    if not m:
        return None
    parts, corrige = [], False
    g = m.groups()
    for num, unit in ((g[0], g[1]), (g[2], g[3])):
        if not num:
            continue
        fixed = num.translate(_FIX)
        if not fixed.isdigit():
            return None
        if fixed != num:
            corrige = True
        parts.append((int(fixed), unit))
    for i, (v, u) in enumerate(parts):
        if i > 0 and v >= {"h": 24, "min": 60, "s": 60}[u]:
            return None
    if parts[0][1] == "j" and parts[0][0] > 30:
        return None
    if all(v == 0 for v, _ in parts):
        return None
    sec = sum(v * UNIT_S[u] for v, u in parts)
    return Duree(sec, PAS[parts[0][1]], " ".join(f"{v}{u}" for v, u in parts), corrige)


def fmt_duree(sec: float) -> str:
    if sec is None or math.isinf(sec):
        return "∞"
    sec = int(round(sec))
    if sec < 0:
        return "-" + fmt_duree(-sec)
    j, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    if j:
        return f"{j}j {h}h {m:02d}min"
    if h:
        return f"{h}h {m:02d}min"
    if m:
        return f"{m}min {s:02d}s"
    return f"{s}s"


# --------------------------------------------------------------------------
# OCR (RapidOCR)
# --------------------------------------------------------------------------
_ocr_engine = None


def ocr_engine():
    global _ocr_engine
    if _ocr_engine is None:
        import logging
        from rapidocr import RapidOCR
        logging.getLogger("RapidOCR").setLevel(logging.WARNING)
        _ocr_engine = RapidOCR()
    return _ocr_engine


@dataclass
class Mot:
    texte: str
    x1: int
    y1: int
    x2: int
    y2: int
    score: float

    @property
    def h(self):
        return self.y2 - self.y1

    @property
    def yc(self):
        return (self.y1 + self.y2) / 2


def ocr(img: Image.Image, dx: int = 0, dy: int = 0) -> list[Mot]:
    import numpy as np
    r = ocr_engine()(np.array(img), use_det=True, use_cls=True, use_rec=True)
    mots = []
    if r.boxes is None:
        return mots
    for box, txt, sc in zip(r.boxes, r.txts, r.scores):
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        mots.append(Mot(txt.strip(), int(min(xs)) + dx, int(min(ys)) + dy, int(max(xs)) + dx,
                        int(max(ys)) + dy, float(sc)))
    return mots


def lire_ligne(img: Image.Image, rect, zoom=2.0) -> tuple[str, float]:
    """Reconnaissance seule (sans détection) d'une case : très rapide (~10 ms)."""
    import numpy as np
    c = img.crop(tuple(int(v) for v in rect))
    if c.width < 4 or c.height < 4:
        return "", 0.0
    c = c.resize((int(c.width * zoom), int(c.height * zoom)), Image.LANCZOS)
    r = ocr_engine()(np.array(c), use_det=False, use_cls=False, use_rec=True)
    if not r.txts:
        return "", 0.0
    return r.txts[0], float(r.scores[0])


def lire_case_temps(img: Image.Image, cell) -> tuple[Duree | None, str]:
    """cell = (x_droite, y1, y2, h). On essaie des largeurs de la plus large à la plus étroite :
    la plus large qui se lit est la bonne (une case trop étroite couperait des chiffres)."""
    xr, y1, y2, h = cell
    vus = []
    for k in (5.2, 4.6, 4.0, 3.4):
        txt, sc = lire_ligne(img, (xr - k * h, y1 - 0.25 * h, xr + 0.15 * h, y2 + 0.25 * h))
        d = parse_duree(txt)
        vus.append(txt)
        if d:
            return d, txt
    return None, " / ".join(t for t in vus if t)


def _meme_ligne(a: Mot, b: Mot) -> bool:
    inter = min(a.y2, b.y2) - max(a.y1, b.y1)
    return inter > 0.5 * min(a.h, b.h)


def _lettres(t: str) -> int:
    return sum(c.isalpha() for c in t)


def _ressemble_duree(t: str) -> bool:
    s = re.sub(r"[^0-9a-zü]", "", t.lower())
    if not (1 <= len(s) <= 10):
        return False
    lettres = [c for c in s if c.isalpha()]
    return len(lettres) <= 5 and all(c in "jhmins" + "oiluüsbgzt" for c in lettres) and s[-1] in "jhns"


def _fusionner_durees(mots: list[Mot]) -> list[Mot]:
    """OCR coupe parfois '6h' '48min' en deux boîtes : on recolle."""
    mots = sorted(mots, key=lambda m: (round(m.yc / 10), m.x1))
    out: list[Mot] = []
    for m in mots:
        if out:
            p = out[-1]
            if (_meme_ligne(p, m) and 0 <= m.x1 - p.x2 < 1.2 * max(p.h, m.h)
                    and parse_duree(p.texte) and parse_duree(m.texte)
                    and parse_duree(p.texte + m.texte)):
                out[-1] = Mot(p.texte + " " + m.texte, p.x1, min(p.y1, m.y1), m.x2,
                              max(p.y2, m.y2), min(p.score, m.score))
                continue
        out.append(m)
    return out


def _est_suggestion(img: Image.Image, x1: int, x2: int, y: float) -> bool:
    """Lignes 'Améliorations suggérées' : fond beige clair. Actives : brun foncé / vert."""
    if x2 <= x1 + 4:
        return False
    xs = range(int(x1), int(x2), max(1, int(x2 - x1) // 40))
    px = [img.getpixel((x, int(y))) for x in xs]
    beige = sum(1 for r, g, b in px if 140 <= r <= 215 and 100 <= g <= 165 and 70 <= b <= 135
                and r - g > 22 and g - b > 15)
    return beige > 0.5 * len(px)


@dataclass
class Amelioration:
    nom: str
    categorie: str
    affiche: str          # texte lu à l'écran
    travail_s: float      # travail restant (borne basse de l'affichage, ou valeur exacte si precis)
    pas_s: int            # précision de l'affichage (0 = exact)
    capture_epoch: float  # heure téléphone à laquelle travail_s est valable
    actif: bool = True
    note: str = ""
    box: tuple = ()
    illisible: bool = False
    cellule: tuple = ()   # (x_droite, y1, y2, h) de la case du timer
    precis: bool = False
    vitesse_mesuree: float | None = None
    vitesse_max: float | None = None
    source: str = "ecran"     # "ecran" (OCR), "export" (export du jeu), "main"
    boostable: bool = True    # False : simple minuteur (repos des assistants, tour de l'horloge)


@dataclass
class Boost:
    type: str             # clé BOOST_TYPES
    reste_s: float        # durée restante (temps réel) au moment capture_epoch
    capture_epoch: float
    actif: bool = True
    cible: str = ""       # nom de l'amélioration (apprenti/assistant)
    debut_dans_s: float = 0
    score_icone: float = -1
    box: tuple = ()
    texte: str = ""
    inconnu: bool = False
    rect_texte: tuple = ()  # case du timer de la bulle (pour le suivi)
    prevu: bool = False     # ajouté depuis un conseil


def _vecteur(img: Image.Image):
    """Histogramme des teintes (pixels colorés) : stable même si le décor derrière bouge."""
    import numpy as np
    a = np.asarray(img.convert("HSV").resize((32, 32), Image.BILINEAR), dtype=np.float32)
    h, sat, v = a[..., 0], a[..., 1], a[..., 2]
    m = (sat > 70) & (v > 70)
    hist, _ = np.histogram(h[m], bins=18, range=(0, 256))
    return hist / max(1, hist.sum())


def reconnaitre_icone(icon: Image.Image, seuil: float):
    """Renvoie (type, distance). Distance 0 = identique, 2 = rien à voir."""
    import numpy as np
    v = _vecteur(icon)
    par_type: dict[str, float] = {}
    fichiers = list(ICON_DIR.glob("*.png")) if ICON_DIR.exists() else []
    fichiers += list(BASE_ICON_DIR.glob("*.png"))
    for f in fichiers:
        typ = f.stem.split("__")[0]
        if typ not in BOOST_TYPES:
            continue
        try:
            d = float(np.abs(_vecteur(Image.open(f)) - v).sum())
        except Exception:
            continue
        par_type[typ] = min(d, par_type.get(typ, 1e9))
    if not par_type:
        return None, 9.0
    tri = sorted(par_type.items(), key=lambda kv: kv[1])
    best, best_d = tri[0]
    second = tri[1][1] if len(tri) > 1 else 9.0
    if best_d <= seuil and best_d < 0.7 * second:
        return best, best_d
    return None, best_d


def apprendre_icone(icon: Image.Image, typ: str):
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    icon.save(ICON_DIR / f"{typ}__{int(time.time() * 1000)}.png")


def icone_rect(m: Mot, limite_x: int):
    h = m.h
    return (int(m.x2 + 0.1 * h), int(m.yc - 1.3 * h), int(min(m.x2 + 1.8 * h, limite_x)), int(m.yc + 0.8 * h))


TITRES = [
    (("bâtiment", "batiment", "timents", "héros", "heros"), "Ouvriers"),
    (("recherche", "laboratoire", "labo"), "Labo"),
    (("animal", "animaux", "familier"), "Animalerie"),
    (("forge",), "Forge"),
    (("horloge", "base des ouvriers", "maître", "maitre"), "Base ouvriers"),
]


@dataclass
class ResultatScan:
    ameliorations: list
    boosts: list
    categorie_detectee: str | None
    image: Image.Image
    epoch: float
    icones: dict = field(default_factory=dict)  # index boost -> Image
    panneau: tuple = ()                          # (x_gauche, x_droite)
    messages: list = field(default_factory=list)


def _mediane(v):
    v = sorted(v)
    return v[len(v) // 2] if v else None


def analyser(img: Image.Image, epoch: float, cfg: dict, categorie_forcee: str | None = None) -> ResultatScan:
    W, H = img.size
    x0 = int(W * 0.35)
    mots = _fusionner_durees(ocr(img.crop((x0, 0, W, H)), dx=x0))
    msgs = []

    temps = [(m, parse_duree(m.texte)) for m in mots]
    temps = [(m, d) for m, d in temps if d]
    ids_t = {id(m) for m, _ in temps}
    noms = [m for m in mots if _lettres(m.texte) >= 3 and id(m) not in ids_t and not _ressemble_duree(m.texte)]

    # catégorie (titre du menu)
    cat = None
    for m in sorted(mots, key=lambda m: m.y1):
        low = m.texte.lower()
        for keys, c in TITRES:
            if any(k in low for k in keys):
                cat = c
                break
        if cat:
            break
    categorie = categorie_forcee or cat or "Ouvriers"
    y_sugg = min((m.y1 for m in mots if "sugg" in m.texte.lower()), default=None)

    # 1) colonne de droite des timers du menu : durées avec un nom à leur gauche
    paires = []
    for m, d in temps:
        g = [n for n in noms if _meme_ligne(n, m) and n.x2 <= m.x1 + 5]
        if g:
            paires.append((m, d, min(g, key=lambda n: n.x1)))
    if not paires:
        return ResultatScan([], [], cat, img, epoch, {}, (), ["Menu des ouvriers pas trouvé à l'écran."])
    xr = _mediane([m.x2 for m, _, _ in paires])
    paires = [p for p in paires if abs(p[0].x2 - xr) < 0.03 * W]
    # colonne de gauche des noms (lignes en cours) : x1 le plus fréquent
    xl = _mediane([n.x1 for _, _, n in paires if y_sugg is None or n.y1 < y_sugg]) or _mediane([n.x1 for _, _, n in paires])
    h_ligne = _mediane([n.h for _, _, n in paires]) or 30
    panneau_g = int(xl - 0.7 * h_ligne)

    # 2) lignes = noms alignés sur la colonne (même si l'OCR a raté leur timer)
    lignes = []
    for n in sorted(noms, key=lambda n: n.y1):
        if abs(n.x1 - xl) > 1.2 * h_ligne or n.x1 < panneau_g:
            continue
        if y_sugg is not None and n.y1 >= y_sugg - 0.3 * h_ligne:
            continue
        if any(abs(n.yc - l.yc) < 0.5 * h_ligne for l in lignes):
            continue
        lignes.append(n)

    ams = []
    for n in lignes:
        if _est_suggestion(img, n.x2 + 5, xr - 4 * h_ligne, n.yc):
            continue
        # nom complet : tous les morceaux de texte de la ligne avant la colonne des timers
        morceaux = sorted([o for o in noms if _meme_ligne(o, n) and o.x1 >= n.x1 - 5 and o.x2 < xr - 3 * h_ligne],
                          key=lambda o: o.x1)
        texte = " ".join(o.texte for o in morceaux) or n.texte
        texte = texte.strip()
        texte = texte[:1].upper() + texte[1:].lower()
        cell = (xr, n.y1, n.y2, n.h)
        d_det = next((d for m, d, nn in paires if _meme_ligne(m, n)), None)
        d_rec, brut = lire_case_temps(img, cell)
        note = []
        if d_det and d_rec and d_det.secondes != d_rec.secondes:
            # désaccord des 2 lectures : on relit plus gros
            d3, _ = lire_case_temps(img.resize((W * 2, H * 2), Image.LANCZOS), tuple(v * 2 for v in cell))
            if d3 and d3.secondes in (d_det.secondes, d_rec.secondes):
                d = d3
            else:
                d = d_rec
            note.append(f"2 lectures différentes ({d_det.texte} / {d_rec.texte}) : vérifie")
        else:
            d = d_rec or d_det
            if d and not (d_det and d_rec):
                note.append("lu 1 seule fois : vérifie")
        box = (n.x1, n.y1, xr, n.y2)
        if d is None:
            ams.append(Amelioration(texte, categorie, "?? " + brut, 0, 60, epoch, actif=False,
                                    note="DURÉE ILLISIBLE : écris-la à la main", box=box,
                                    illisible=True, cellule=cell))
            continue
        if d.corrige and not note:
            note.append("lettre lue comme chiffre")
        if d.pas >= 3600:
            note.append("précision ±1 h")
        ams.append(Amelioration(texte, categorie, d.texte, d.secondes, d.pas, epoch, actif=True,
                                note=", ".join(note), box=box, cellule=cell))

    # 3) bulles de boost : timer seul, juste à gauche du menu, de la taille du texte du menu,
    #    et PAS dans la colonne des ressources (bouclier sous les gemmes)
    chiffres = [m for m in mots if re.fullmatch(r"[\d\s.,]{3,}", m.texte)]
    boosts, icones = [], {}
    for m, d in temps:
        if any(m is p[0] for p in paires):
            continue
        if not (panneau_g - 6 * h_ligne <= m.x2 <= panneau_g + 0.5 * h_ligne):
            continue
        if m.h < 0.6 * h_ligne:
            continue
        hud = any(abs(c.x2 - m.x2) < 1.2 * m.h and -0.2 * m.h <= m.y1 - c.y2 < 2.5 * m.h for c in chiffres)
        if hud:
            continue  # bouclier / ressources
        ib = icone_rect(m, panneau_g)
        icon = img.crop(ib)
        typ, dist = reconnaitre_icone(icon, float(cfg.get("seuil_icone_hist", 0.6)))
        b = Boost(typ or "potion_ouvrier", d.secondes + (0.5 if d.pas == 1 else d.pas / 2), epoch,
                  actif=bool(typ) and typ != "ignorer", score_icone=dist,
                  box=(m.x1, min(m.y1, ib[1]), ib[2], max(m.y2, ib[3])), texte=d.texte, inconnu=typ is None,
                  rect_texte=(m.x1 - 0.3 * m.h, m.y1 - 0.2 * m.h, m.x2 + 0.3 * m.h, m.y2 + 0.2 * m.h))
        icones[len(boosts)] = icon
        boosts.append(b)
    if not ams:
        msgs.append("Aucune amélioration en cours lue.")
    return ResultatScan(ams, boosts, cat, img, epoch, icones, (panneau_g, xr), msgs)


# --------------------------------------------------------------------------
# Scan précis : on relit les cases en boucle et on note l'instant où la minute change
# --------------------------------------------------------------------------
@dataclass
class _Piste:
    valeur: float | None = None
    t: float = 0.0
    flips: list = field(default_factory=list)   # (t_flip, reste_au_flip, demi_fenetre)
    conflits: int = 0
    pas: int = 60


class Suivi:
    """Le jeu affiche floor(reste / unité). Quand l'affichage passe de v+u à v,
    le reste vaut exactement v+u à cet instant (on prend l'hypothèse « jamais trop tôt »)."""

    def __init__(self, ams: list[Amelioration], boosts: list[Boost]):
        self.ams = [a for a in ams if a.cellule and not a.illisible and a.pas_s <= 60]
        self.bulles = [b for b in boosts if b.rect_texte]
        self.pistes = {id(a): _Piste(a.travail_s, a.capture_epoch, pas=max(1, a.pas_s)) for a in self.ams}
        self.debut = time.time()
        self.lectures = 0

    def pas_fini(self) -> list[Amelioration]:
        return [a for a in self.ams if not self.pistes[id(a)].flips]

    def fini(self, t: float, attente_vitesse=10.0) -> bool:
        """Chaque ligne a vu un changement, et soit 2 changements (vitesse mesurée),
        soit assez d'attente après le 1er pour savoir que ce n'est pas boosté."""
        for a in self.ams:
            f = self.pistes[id(a)].flips
            if not f:
                return False
            if len(f) < 2 and t - f[0][0] < attente_vitesse:
                return False
        return True

    def traiter(self, img: Image.Image, t: float) -> list[str]:
        self.lectures += 1
        evts = []
        for a in self.ams:
            p = self.pistes[id(a)]
            d, _ = lire_case_temps(img, a.cellule)
            if d is None:
                continue
            v, u = d.secondes, max(1, d.pas)
            if p.valeur is None:
                p.valeur, p.t, p.pas = v, t, u
                continue
            if v == p.valeur:
                p.t, p.conflits = t, 0
                continue
            dt = t - p.t
            if v < p.valeur and (p.valeur - v) <= dt * 40 + 2 * u:
                reste_flip = v + u
                p.flips.append(((p.t + t) / 2, reste_flip, dt / 2))
                p.valeur, p.t, p.pas, p.conflits = v, t, u, 0
                a.affiche = d.texte
                evts.append(f"{a.nom} : {d.texte} (changement vu à ±{dt / 2:.1f} s)")
            else:
                p.conflits += 1
                if p.conflits >= 3:  # 3 lectures d'accord : on repart de cette valeur
                    p.valeur, p.t, p.pas, p.conflits, p.flips = v, t, u, 0, []
        for b in self.bulles:
            txt, _ = lire_ligne(img, b.rect_texte)
            d = parse_duree(txt)
            if d and d.pas == 1 and abs((d.secondes + 0.5) - (b.reste_s - (t - b.capture_epoch))) < 30:
                b.reste_s, b.capture_epoch, b.texte = d.secondes + 0.5, t, d.texte
        return evts

    def appliquer(self):
        """Recale chaque amélioration sur son changement de minute le plus précis + vitesse."""
        for a in self.ams:
            p = self.pistes[id(a)]
            if not p.flips:
                continue
            tf, reste, demi = min(p.flips, key=lambda f: f[2])
            a.capture_epoch, a.travail_s, a.pas_s, a.precis = tf, float(reste), 0, True
            a.vitesse_mesuree, a.vitesse_max = None, None
            if len(p.flips) >= 2:
                (t1, r1, _), (t2, r2, _) = p.flips[0], p.flips[-1]
                if t2 - t1 > 1:
                    a.vitesse_mesuree = (r1 - r2) / (t2 - t1)
            elif p.t - p.flips[0][0] > 1:
                # un seul changement : la vitesse est au plus 1 unité / temps écoulé depuis
                a.vitesse_max = p.pas / (p.t - p.flips[0][0])
            a.note = f"précis ±{max(1, round(demi))} s" + (
                f", vitesse mesurée x{a.vitesse_mesuree:.1f}" if a.vitesse_mesuree else "")


# --------------------------------------------------------------------------
# Calcul du temps réel restant
# --------------------------------------------------------------------------
def segments_bonus(am: Amelioration, boosts: list[Boost], cfg: dict, ref_epoch: float):
    """Liste de (debut, fin, bonus, groupe) en secondes relatives à ref_epoch."""
    segs = []
    if not getattr(am, "boostable", True):
        return segs
    for b in boosts:
        if not b.actif or b.type == "ignorer" or b.inconnu:
            continue
        debut = (b.capture_epoch - ref_epoch) + b.debut_dans_s
        fin = debut + b.reste_s
        if b.type == "apprenti":
            if b.cible != am.nom or am.categorie not in ("Ouvriers", "Forge"):
                continue
            segs.append((debut, fin, int(cfg["niveau_apprenti"]), None))
        elif b.type == "assistant":
            if b.cible != am.nom or am.categorie != "Labo":
                continue
            segs.append((debut, fin, int(cfg["niveau_assistant"]), None))
        else:
            lib, bonus, cats, groupe = BOOST_TYPES[b.type]
            if am.categorie in cats:
                if cfg.get("potion_snack_cumul"):
                    groupe = None
                segs.append((debut, fin, bonus, groupe))
    return segs


def vitesse_a(segs, t: float) -> float:
    libre, groupes = 0, {}
    for s, e, b, g in segs:
        if s <= t < e:
            if g is None:
                libre += b
            else:
                groupes[g] = max(groupes.get(g, 0), b)
    return 1 + libre + sum(groupes.values())


def temps_reel(travail_s: float, segs, t0: float = 0.0) -> float:
    """Temps réel (s) à partir de t0 pour faire `travail_s` de travail."""
    if travail_s <= 0:
        return 0.0
    pts = sorted({t0} | {s for s, e, _, _ in segs if s > t0} | {e for s, e, _, _ in segs if e > t0})
    reste = float(travail_s)
    for i, p in enumerate(pts):
        nxt = pts[i + 1] if i + 1 < len(pts) else math.inf
        v = vitesse_a(segs, p)
        if v * (nxt - p) >= reste:
            return p + reste / v - t0
        reste -= v * (nxt - p)
    return math.inf


def travail_fait(segs, t0: float, t1: float) -> float:
    """Travail fait entre t0 et t1 (secondes relatives)."""
    pts = sorted({t0, t1} | {x for s, e, _, _ in segs for x in (s, e) if t0 < x < t1})
    return sum(vitesse_a(segs, a) * (b - a) for a, b in zip(pts, pts[1:]))


def travail_restant(am, boosts, cfg, t: float) -> float:
    segs = segments_bonus(am, boosts, cfg, am.capture_epoch)
    return am.travail_s - travail_fait(segs, 0.0, t - am.capture_epoch)


@dataclass
class Prevision:
    am: Amelioration
    fin_epoch: float
    fin_min: float
    fin_max: float
    vitesse_actuelle: float


def prevoir(am: Amelioration, boosts: list[Boost], cfg: dict, now: float | None = None) -> Prevision:
    ref = am.capture_epoch
    segs = segments_bonus(am, boosts, cfg, ref)
    lo = temps_reel(am.travail_s, segs)
    hi = temps_reel(am.travail_s + am.pas_s, segs)
    # affichage 'Xj Yh' (±1 h) : le plus tôt -> l'alarme sert de rappel pour re-scanner.
    # sinon : le plus tard possible -> l'alarme ne sonne jamais avant la fin.
    est = lo if am.pas_s >= 3600 else hi
    t = 0.0 if now is None else max(0.0, now - ref)
    return Prevision(am, ref + est, ref + lo, ref + hi, vitesse_a(segs, t))


@dataclass
class AlarmePrevue:
    epoch: int
    label: str = ""
    noms: list = field(default_factory=list)
    approx: bool = False


def planifier(prevs: list[Prevision], now_epoch: float, cfg: dict) -> tuple[list[AlarmePrevue], list[str]]:
    fenetre = float(cfg.get("fenetre_heures", 24)) * 3600
    avance = int(cfg.get("avance_minutes", 0)) * 60
    groupes: dict[int, AlarmePrevue] = {}
    infos = []
    for p in prevs:
        if not p.am.actif or p.am.illisible:
            continue
        t = p.fin_epoch - avance
        t = math.ceil(t / 60) * 60
        if t - now_epoch < 60:
            infos.append(f"{p.am.nom} : fini dans moins d'1 min, pas d'alarme")
            continue
        if t - now_epoch > min(fenetre, 24 * 3600 - 60):
            continue
        a = groupes.setdefault(t, AlarmePrevue(t))
        a.noms.append(p.am.nom)
        a.approx = a.approx or p.am.pas_s >= 3600
    alarmes = sorted(groupes.values(), key=lambda a: a.epoch)
    for a in alarmes:
        lab = "CoC: " + " + ".join(a.noms)
        if a.approx:
            lab += " (≈)"
        a.label = lab[:80]
    return alarmes, infos


@dataclass
class Sortie:
    epoch: int
    label: str
    noms: list
    approx: bool
    nuit: bool
    alarme: bool
    event: bool


def repartir(prevs: list[Prevision], now_epoch: float, cfg: dict) -> list[Sortie]:
    """Le jour -> alarme (et/ou event), la nuit -> event Google Agenda (selon les réglages)."""
    ev = Eveil(cfg.get("eveil_debut", "07:00"), cfg.get("eveil_fin", "23:00"))
    fen_alarme = min(float(cfg.get("fenetre_heures", 24)) * 3600, 24 * 3600 - 60)
    fen_event = float(cfg.get("horizon_event_h", 168)) * 3600
    avance = int(cfg.get("avance_minutes", 0)) * 60
    groupes: dict[int, Sortie] = {}
    actives = set(cfg.get("categories_actives", CATEGORIES))
    for p in prevs:
        if not p.am.actif or p.am.illisible or math.isinf(p.fin_epoch) or p.am.categorie not in actives:
            continue
        t = int(math.ceil((p.fin_epoch - avance) / 60) * 60)
        if t - now_epoch < 60:
            continue
        s = groupes.setdefault(t, Sortie(t, "", [], False, not ev.eveille(t), False, False))
        s.noms.append(p.am.nom)
        s.approx = s.approx or p.am.pas_s >= 3600
    out = []
    for t, s in sorted(groupes.items()):
        mode = cfg.get("mode_nuit", "event") if s.nuit else cfg.get("mode_jour", "alarme")
        s.alarme = "alarme" in mode and t - now_epoch <= fen_alarme
        s.event = "event" in mode and t - now_epoch <= fen_event
        if not (s.alarme or s.event):
            continue
        s.label = ("CoC: " + " + ".join(s.noms) + (" (≈)" if s.approx else ""))[:90]
        out.append(s)
    return out


# --------------------------------------------------------------------------
# Conseils : quand boire la potion d'ouvrier / sur quoi mettre l'apprenti
# pour que les améliorations finissent quand tu es réveillé
# --------------------------------------------------------------------------
def _hm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


class Eveil:
    def __init__(self, debut="07:00", fin="23:00"):
        self.d = _hm(debut)
        self.f = _hm(fin)

    def eveille(self, epoch: float) -> bool:
        t = datetime.fromtimestamp(epoch)
        m = t.hour * 60 + t.minute
        d, f = self.d[0] * 60 + self.d[1], self.f[0] * 60 + self.f[1]
        return d <= m < f if d < f else (m >= d or m < f)

    def prochain_reveil(self, epoch: float) -> float:
        """Prochain instant éveillé >= epoch."""
        if self.eveille(epoch):
            return epoch
        t = datetime.fromtimestamp(epoch)
        r = t.replace(hour=self.d[0], minute=self.d[1], second=0, microsecond=0)
        if r <= t:
            r += timedelta(days=1)
        return r.timestamp()

    def attente(self, epoch: float) -> float:
        """Temps où l'ouvrier reste sans rien faire si ça finit à `epoch`."""
        return self.prochain_reveil(epoch) - epoch


@dataclass
class Plan:
    titre: str
    potion_t: float | None
    apprenti: tuple | None          # (nom, t)
    fins: dict                      # nom -> epoch de fin
    nuit: int
    attente_s: float
    gain_s: float
    potion_plage: tuple | None = None


def conseiller(ams: list[Amelioration], boosts: list[Boost], cfg: dict, now: float) -> tuple[Plan, list[Plan]]:
    ev = Eveil(cfg.get("eveil_debut", "07:00"), cfg.get("eveil_fin", "23:00"))
    horizon = now + float(cfg.get("horizon_conseil_h", 96)) * 3600
    rows = [a for a in ams if a.actif and not a.illisible and a.categorie == "Ouvriers"]
    if not rows:
        return None, []
    lvl = int(cfg.get("niveau_apprenti", 1))
    # fin de la potion déjà active (on n'en boit pas une 2e par-dessus)
    fin_potion = now
    for b in boosts:
        if b.actif and not b.inconnu and b.type == "potion_ouvrier":
            fin_potion = max(fin_potion, b.capture_epoch + b.debut_dans_s + b.reste_s)
    apprenti_deja = any(b.actif and b.type == "apprenti" and b.cible for b in boosts)

    def simuler(potion_t, app):
        extra = []
        if potion_t is not None:
            extra.append(Boost("potion_ouvrier", 3600, potion_t, prevu=True))
        if app is not None:
            extra.append(Boost("apprenti", 3600, app[1], cible=app[0], prevu=True))
        fins = {}
        for a in rows:
            p = prevoir(a, boosts + extra, cfg)
            fins[a.nom] = p.fin_epoch
        nuit = sum(1 for f in fins.values() if f <= horizon and not ev.eveille(f))
        att = sum(ev.attente(f) for f in fins.values() if f <= horizon)
        return fins, nuit, att

    base_fins, base_nuit, base_att = simuler(None, None)
    base = Plan("Sans rien faire", None, None, base_fins, base_nuit, base_att, 0.0)

    # moments possibles pour boire la potion : maintenant, puis toutes les 15 min éveillé (24 h)
    potions = [None]
    if int(cfg.get("potions_dispo", 0)) > 0:
        t = max(now, fin_potion)
        while t < now + 24 * 3600:
            if ev.eveille(t):
                potions.append(t)
            t += 900
    apprentis = [None]
    if cfg.get("apprenti_dispo", True) and not apprenti_deja:
        ta = ev.prochain_reveil(now + 60 * float(cfg.get("apprenti_dispo_dans_min", 0)))
        for a in rows:
            apprentis.append((a.nom, ta))

    plans = []
    for pt in potions:
        for ap in apprentis:
            if pt is None and ap is None:
                continue
            fins, nuit, att = simuler(pt, ap)
            gain = sum(base_fins[n] - fins[n] for n in fins if base_fins[n] <= horizon or fins[n] <= horizon)
            plans.append(Plan("", pt, ap, fins, nuit, att, gain))

    def cle(p: Plan):
        # 1) le moins d'attente la nuit  2) le plus de temps gagné  3) le plus tôt
        return (round(p.attente_s / 600), p.nuit, -round(p.gain_s / 600), p.potion_t or 0)

    plans.sort(key=cle)
    choisis, vus = [], set()

    def ajoute(p: Plan, titre):
        k = (p.potion_t, p.apprenti)
        if p is None or k in vus:
            return
        vus.add(k)
        p.titre = titre
        if p.potion_t is not None:
            # plage de moments de potion qui donnent le même résultat
            memes = [q.potion_t for q in plans if q.apprenti == p.apprenti and q.potion_t is not None
                     and cle(q)[:3] == cle(p)[:3]]
            if memes:
                p.potion_plage = (min(memes), max(memes))
        choisis.append(p)

    if plans:
        ajoute(plans[0], "Meilleur plan")
    maintenant = next((p for p in plans if p.apprenti is None and p.potion_t is not None
                       and p.potion_t - max(now, fin_potion) < 60), None)
    seul_p = next((p for p in plans if p.apprenti is None), None)
    seul_a = next((p for p in plans if p.potion_t is None), None)
    if seul_a:
        ajoute(seul_a, "Apprenti seul")
    if seul_p:
        ajoute(seul_p, "Potion seule")
    if maintenant:
        ajoute(maintenant, "Potion tout de suite")
    for p in plans[1:6]:
        ajoute(p, "Autre option")
    return base, choisis


# --------------------------------------------------------------------------
# Suppression des anciennes alarmes "CoC: " (Horloge Samsung ne gère pas
# DISMISS_ALARM pour une alarme à venir -> on pilote l'écran via uiautomator)
# --------------------------------------------------------------------------
PREFIXE = "CoC: "
_NODE = re.compile(r"<node [^>]*>")


def _attr(n, k):
    m = re.search(k + r'="([^"]*)"', n)
    return m.group(1) if m else ""


def _centre(bounds):
    x1, y1, x2, y2 = map(int, re.findall(r"\d+", bounds))
    return (x1 + x2) // 2, (y1 + y2) // 2


def _unescape(s):
    return (s.replace("&amp;", "&").replace("&quot;", '"').replace("&apos;", "'")
            .replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'"))


def _dump(adb: Adb):
    for _ in range(3):
        try:
            adb.shell("uiautomator dump /sdcard/coc_ui.xml", timeout=20)
            x = adb.shell("cat /sdcard/coc_ui.xml")
            if "<hierarchy" in x:
                return _NODE.findall(x)
        except AdbError:
            pass
        time.sleep(0.8)
    raise AdbError("impossible de lire l'écran de l'Horloge (uiautomator)")


def _rid(n):
    return _attr(n, "resource-id").split("/")[-1]


def supprimer_alarmes_coc(adb: Adb, log=print, max_suppr=60) -> int:
    """Supprime dans Horloge Samsung toutes les alarmes dont le libellé commence par 'CoC: '."""
    adb.show_alarms()
    time.sleep(1.8)
    nodes = _dump(adb)
    liste = next((n for n in nodes if _rid(n) == "alarm_list"), None)
    if liste is None:
        raise AdbError("liste des alarmes non affichée (téléphone verrouillé ?)")
    lx, _ = _centre(_attr(liste, "bounds"))
    _, y1, _, y2 = map(int, re.findall(r"\d+", _attr(liste, "bounds")))
    haut, bas = y1 + 150, y2 - 150
    suppr, pages = 0, 0
    for _ in range(4):
        adb.shell(f"input swipe {lx} {haut} {lx} {bas} 250")
    time.sleep(0.6)
    while suppr < max_suppr:
        nodes = _dump(adb)
        cible = next((n for n in nodes if _rid(n) == "alarm_onoff_switch"
                      and _unescape(_attr(n, "content-desc")).startswith(PREFIXE)), None)
        if cible is None:
            avant = [_attr(n, "content-desc") for n in nodes if _rid(n) == "alarm_onoff_switch"]
            adb.shell(f"input swipe {lx} {bas} {lx} {haut} 350")
            time.sleep(0.8)
            apres = [_attr(n, "content-desc") for n in _dump(adb) if _rid(n) == "alarm_onoff_switch"]
            pages += 1
            if apres == avant or pages > 25:
                break
            continue
        nom = _unescape(_attr(cible, "content-desc"))
        cx, cy = _centre(_attr(cible, "bounds"))
        adb.shell(f"input swipe {cx - 400} {cy} {cx - 400} {cy} 900")  # appui long
        time.sleep(1.2)
        nodes = _dump(adb)
        coches = [_unescape(_attr(n, "content-desc")) for n in nodes
                  if _rid(n) == "alarmListCheckBox" and _attr(n, "checked") == "true"]
        btn = next((n for n in nodes if _rid(n) == "delete"), None)
        annuler = next((n for n in nodes if _rid(n) == "selection_cancel"), None)
        # sécurité : on ne supprime QUE si une seule alarme est cochée et que c'est une alarme CoC
        if btn is None or len(coches) != 1 or not coches[0].startswith(PREFIXE):
            if annuler is not None:
                adb.shell("input tap %d %d" % _centre(_attr(annuler, "bounds")))
            else:
                adb.shell("input keyevent KEYCODE_BACK")
            raise AdbError(f"sélection inattendue ({coches}), arrêt par sécurité")
        adb.shell("input tap %d %d" % _centre(_attr(btn, "bounds")))
        time.sleep(1.0)
        suppr += 1
        pages = 0
        log(f"supprimée : {nom.split(' ,')[0]}")
    return suppr
