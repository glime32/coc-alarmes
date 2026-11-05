"""Lecture de l'export officiel du jeu (Paramètres → Plus de paramètres → Export de données → Copier).
JSON : {"tag", "timestamp", "buildings":[{"data", "lvl", "timer"}...], "helpers":[...], "boosts":{...}}
"timer" = secondes de travail restantes au moment "timestamp" (exact à la seconde)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from core import Amelioration, fmt_duree
from noms import Noms

# section de l'export -> catégorie du programme
SECTIONS = {
    "buildings": "Ouvriers",
    "traps": "Ouvriers",
    "heroes": "Ouvriers",
    "units": "Labo",
    "spells": "Labo",
    "siege_machines": "Labo",
    "pets": "Animalerie",
    "buildings2": "Base ouvriers",
    "traps2": "Base ouvriers",
    "heroes2": "Base ouvriers",
    "units2": "Base ouvriers",
}
CLES_CONNUES = {"data", "lvl", "timer", "cnt", "gear_up", "weapon", "supercharge", "extra"}

APPRENTI, ASSISTANT = 93000000, 93000001
JOURNEE = 23 * 3600   # journée de travail des assistants
TRAVAIL = 3600        # ils travaillent 1 h


@dataclass
class Export:
    tag: str
    timestamp: int
    ameliorations: list
    niveau_apprenti: int | None = None
    niveau_assistant: int | None = None
    repos_s: int | None = None            # temps avant que les assistants soient dispo
    assistant_au_travail_s: int | None = None  # un assistant travaille encore X s (cible inconnue)
    boosts_bruts: dict = field(default_factory=dict)
    inconnus: list = field(default_factory=list)


def est_export(texte: str) -> bool:
    t = texte.strip()
    return t.startswith("{") and '"timestamp"' in t and '"tag"' in t and '"buildings"' in t


def lire_export(texte: str, noms: Noms) -> Export:
    d = json.loads(texte.strip())
    if not isinstance(d, dict) or "timestamp" not in d or "tag" not in d:
        raise ValueError("ce n'est pas un export de village Clash of Clans")
    ts = int(d["timestamp"])
    ams, inconnus = [], []
    for section, cat in SECTIONS.items():
        for it in d.get(section) or []:
            if not isinstance(it, dict) or not it.get("timer"):
                continue
            gid = int(it.get("data", 0))
            nom = noms.nom(gid)
            if nom is None:
                inconnus.append(gid)
                nom = f"Objet #{gid}"
            lvl = int(it.get("lvl", 0))
            autres = set(it) - CLES_CONNUES
            note = f"exact · niv. {lvl}"
            if it.get("extra"):
                note += " · gobelin"
            if autres:
                note += f" · champs : {', '.join(sorted(autres))}"
            ams.append(Amelioration(
                nom=nom,
                categorie=cat, affiche=fmt_duree(int(it["timer"])), travail_s=float(it["timer"]),
                pas_s=0, capture_epoch=float(ts), actif=True, note=note, precis=True, source="export"))
    ex = Export(d["tag"], ts, ams, boosts_bruts=d.get("boosts") or {}, inconnus=inconnus)
    for h in d.get("helpers") or []:
        if h.get("data") == APPRENTI:
            ex.niveau_apprenti = int(h.get("lvl", 0)) or None
        elif h.get("data") == ASSISTANT:
            ex.niveau_assistant = int(h.get("lvl", 0)) or None
        if "helper_cooldown" in h:
            cd = int(h["helper_cooldown"])
            ex.repos_s = cd if ex.repos_s is None else max(ex.repos_s, cd)
    if ex.repos_s and ex.repos_s > JOURNEE - TRAVAIL:
        ex.assistant_au_travail_s = ex.repos_s - (JOURNEE - TRAVAIL)
    # rappels « bonus » : assistants de nouveau dispo, tour de l'horloge prête
    if ex.repos_s and ex.repos_s > 60:
        ams.append(Amelioration("Assistants dispo (apprenti, labo…)", "Assistants", fmt_duree(ex.repos_s),
                                float(ex.repos_s), 0, float(ts), note="exact · fin du repos", precis=True,
                                source="export", boostable=False))
    cd = int(ex.boosts_bruts.get("clocktower_cooldown", 0) or 0)
    if cd > 60:
        ams.append(Amelioration("Tour de l'horloge prête", "Base ouvriers", fmt_duree(cd), float(cd), 0, float(ts),
                                note="exact · boost gratuit dispo", precis=True, source="export", boostable=False))
    # même nom en double (ex : 2 bombes) -> on numérote
    vus: dict[str, int] = {}
    for a in ams:
        vus[a.nom] = vus.get(a.nom, 0) + 1
    num: dict[str, int] = {}
    for a in ams:
        if vus[a.nom] > 1:
            num[a.nom] = num.get(a.nom, 0) + 1
            a.nom = f"{a.nom} ({num[a.nom]})"
    return ex
