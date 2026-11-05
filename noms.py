"""Noms français des objets du jeu (ID de l'export -> nom), tirés des fichiers officiels du jeu
(serveur d'assets Supercell : logic/*.csv + localization/fr.csv)."""
from __future__ import annotations

import csv
import io
import json
import lzma
import re
import subprocess
import urllib.request
from pathlib import Path

ICI = Path(__file__).resolve().parent
CACHE = Path.home() / ".local/share/coc-alarmes/noms_fr.json"
BASE = ICI / "donnees" / "noms_fr.json"
ASSETS = "https://game-assets.clashofclans.com/{fp}/{f}"
FP_GITHUB = "https://raw.githubusercontent.com/clashperk/clashofclans.js/HEAD/src/util/buildings.json"

# fichier -> classe d'ID (ID = classe * 1 000 000 + rang de la ligne, ou colonne GlobalID)
FICHIERS = {
    "logic/buildings.csv": 1,
    "logic/characters.csv": 4,
    "logic/traps.csv": 12,
    "logic/spells.csv": 26,
    "logic/heroes.csv": 28,
    "logic/pets.csv": 73,
    "logic/villager_apprentices.csv": 93,
}


def _decompresser(d: bytes) -> str:
    if d[:4] == b"Sig:":
        d = d[68:]
    if d[:4] == b"SCLZ":
        raise ValueError("format LZHAM non géré")
    if d[:4] == b"\x28\xb5\x2f\xfd":
        import zstandard
        return zstandard.decompress(d).decode("utf-8")
    d = d[:9] + b"\x00" * 4 + d[9:]
    return lzma.LZMADecompressor().decompress(d).decode("utf-8")


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "coc-alarmes"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def empreinte_telephone(adb_base: list[str]) -> str | None:
    """Lit assets/fingerprint.json dans l'APK installé sur le téléphone."""
    try:
        out = subprocess.run(adb_base + ["shell", "pm path com.supercell.clashofclans"],
                             capture_output=True, text=True, timeout=15).stdout
        apk = next((l.split(":", 1)[1].strip() for l in out.splitlines() if l.strip().endswith("base.apk")), None)
        if not apk:
            return None
        out = subprocess.run(adb_base + ["shell", f"unzip -p {apk} assets/fingerprint.json"],
                             capture_output=True, text=True, timeout=30).stdout
        m = re.search(r'"sha"\s*:\s*"([0-9a-f]{40})"', out)
        return m.group(1) if m else None
    except Exception:
        return None


def empreinte_github() -> str | None:
    try:
        return json.loads(_get(FP_GITHUB))["fingerprint"]
    except Exception:
        return None


def construire(fp: str) -> dict[str, str]:
    fr = {}
    for r in csv.reader(io.StringIO(_decompresser(_get(ASSETS.format(fp=fp, f="localization/fr.csv"))))):
        if len(r) > 1:
            fr[r[0]] = r[1]
    noms: dict[str, str] = {}
    for f, cls in FICHIERS.items():
        try:
            rows = list(csv.reader(io.StringIO(_decompresser(_get(ASSETS.format(fp=fp, f=f))))))
        except Exception:
            continue
        h = rows[0]
        ig = h.index("GlobalID") if "GlobalID" in h else None
        it = h.index("TID") if "TID" in h else None
        rang = 0
        for r in rows[2:]:
            if not r or not r[0].strip():
                continue
            gid = int(r[ig]) if ig is not None and len(r) > ig and r[ig].strip() else cls * 1_000_000 + rang
            rang += 1
            tid = r[it].strip() if it is not None and len(r) > it else ""
            noms[str(gid)] = fr.get(tid) or r[0].strip()
    noms["_empreinte"] = fp
    return noms


class Noms:
    def __init__(self):
        self.d: dict[str, str] = {}
        for p in (BASE, CACHE):
            try:
                self.d.update(json.loads(p.read_text()))
            except Exception:
                pass

    def nom(self, gid: int) -> str | None:
        return self.d.get(str(gid))

    def mettre_a_jour(self, adb_base: list[str] | None = None) -> str:
        fp = (empreinte_telephone(adb_base) if adb_base else None) or empreinte_github()
        if not fp:
            raise RuntimeError("version du jeu introuvable (téléphone et GitHub)")
        if fp == self.d.get("_empreinte"):
            return fp
        noms = construire(fp)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(noms, ensure_ascii=False, indent=0))
        self.d.update(noms)
        return fp
