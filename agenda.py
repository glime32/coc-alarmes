"""Événements Google Agenda pour les fins d'amélioration.
Deux façons :
  - "google" : API Google Agenda (OAuth, une seule fois dans le navigateur).
  - "telephone" : agenda du téléphone via ADB (compte Google du téléphone, synchro auto).
Tous les événements créés portent une marque pour pouvoir les retrouver et les remplacer."""
from __future__ import annotations

import re
import shlex
import time
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path.home() / ".local/share/coc-alarmes"
CLIENT_FILE = DATA_DIR / "google_client.json"
TOKEN_FILE = DATA_DIR / "google_token.json"
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
MARQUE = "coc-alarmes"
FUSEAU = "Europe/Paris"


class AgendaErreur(RuntimeError):
    pass


def titre_propre(t: str) -> str:
    """Le titre d'un event : « CoC · Bombe » (adb refuse les « : » dans les valeurs)."""
    t = t.replace("CoC: ", "CoC · ", 1)
    return t.replace(":", "∶")


def _ok(out: str):
    bas = out.lower()
    if "[error]" in bas or "exception" in bas or "denial" in bas or "usage:" in bas:
        ligne = next((l for l in out.splitlines() if "error" in l.lower() or "exception" in l.lower()
                      or "denial" in l.lower()), out.strip().splitlines()[-1] if out.strip() else "?")
        raise AgendaErreur(ligne.strip())


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------
class AgendaGoogle:
    nom = "Google Agenda (API)"

    def __init__(self, calendrier: str = "primary"):
        self.calendrier = calendrier or "primary"
        self._svc = None

    @staticmethod
    def pret() -> bool:
        return TOKEN_FILE.exists()

    @staticmethod
    def connecter(client_json: str | None = None) -> str:
        """Ouvre le navigateur pour autoriser l'accès (une seule fois)."""
        from google_auth_oauthlib.flow import InstalledAppFlow
        if client_json:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            CLIENT_FILE.write_text(Path(client_json).read_text())
        if not CLIENT_FILE.exists():
            raise AgendaErreur("fichier client OAuth manquant")
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES)
        creds = flow.run_local_server(port=0, open_browser=True,
                                      authorization_prompt_message="",
                                      success_message="CoC Alarmes est connecté à Google Agenda. Tu peux fermer cet onglet.")
        TOKEN_FILE.write_text(creds.to_json())
        TOKEN_FILE.chmod(0o600)
        return "ok"

    def _service(self):
        if self._svc is None:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build
            if not TOKEN_FILE.exists():
                raise AgendaErreur("Google Agenda pas connecté (Paramètres → Connecter Google Agenda)")
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
            if not creds.valid and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                TOKEN_FILE.write_text(creds.to_json())
            self._svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return self._svc

    def supprimer_anciens(self, depuis: float) -> int:
        svc, n, page = self._service(), 0, None
        while True:
            r = svc.events().list(calendarId=self.calendrier, timeMin=_iso(depuis),
                                  privateExtendedProperty=f"{MARQUE}=1", singleEvents=True,
                                  maxResults=250, pageToken=page).execute()
            for ev in r.get("items", []):
                svc.events().delete(calendarId=self.calendrier, eventId=ev["id"]).execute()
                n += 1
            page = r.get("nextPageToken")
            if not page:
                return n

    def creer(self, titre: str, debut: float, duree_min: int = 10, description: str = "", rappels=(10, 0)):
        corps = {
            "summary": titre_propre(titre),
            "description": description,
            "start": {"dateTime": _iso(debut), "timeZone": FUSEAU},
            "end": {"dateTime": _iso(debut + 60 * duree_min), "timeZone": FUSEAU},
            "reminders": {"useDefault": False,
                          "overrides": [{"method": "popup", "minutes": int(m)} for m in sorted(set(rappels))][:5]},
            "extendedProperties": {"private": {MARQUE: "1"}},
            "colorId": "6",
        }
        return self._service().events().insert(calendarId=self.calendrier, body=corps).execute()


# --------------------------------------------------------------------------
class AgendaTelephone:
    """Écrit directement dans l'agenda Android (fournisseur CalendarContract) via ADB."""
    nom = "Agenda du téléphone"

    def __init__(self, adb, compte: str = ""):
        self.adb = adb
        self.compte = compte
        self._cal_id = None

    def calendriers(self) -> list[dict]:
        out = self.adb.shell("content query --uri content://com.android.calendar/calendars "
                             "--projection _id:account_name:account_type:calendar_displayName:calendar_access_level")
        if "Permission Denial" in out or "Exception" in out:
            raise AgendaErreur("le téléphone refuse l'accès à l'agenda par ADB : " + out.strip().splitlines()[0])
        cals = []
        for l in out.splitlines():
            if not l.startswith("Row:"):
                continue
            d = dict(re.findall(r"(\w+)=([^,]*)(?:,|$)", l.split(" ", 2)[2]))
            cals.append({k: v.strip() for k, v in d.items()})
        return cals

    def calendrier_id(self) -> str:
        if self._cal_id:
            return self._cal_id
        cals = [c for c in self.calendriers() if c.get("account_type") == "com.google"
                and int(c.get("calendar_access_level", "0") or 0) >= 500]
        if self.compte:
            cals = [c for c in cals if c.get("account_name") == self.compte]
        if not cals:
            raise AgendaErreur(f"aucun agenda Google modifiable trouvé pour {self.compte or 'le téléphone'}")
        prim = [c for c in cals if c.get("calendar_displayName") == c.get("account_name")]
        self._cal_id = (prim or cals)[0]["_id"]
        return self._cal_id

    def _ids(self, where: str) -> list[str]:
        out = self.adb.shell("content query --uri content://com.android.calendar/events --projection _id --where "
                             + shlex.quote(where))
        return re.findall(r"_id=(\d+)", out)

    def supprimer_anciens(self, depuis: float) -> int:
        cid = self.calendrier_id()
        where = f"calendar_id={cid} AND description LIKE '%{MARQUE}%' AND dtstart>={int(depuis * 1000)} AND deleted=0"
        ids = self._ids(where)
        for i in ids:
            _ok(self.adb.shell(f"content delete --uri content://com.android.calendar/events --where {shlex.quote('_id=' + i)}"))
        return len(ids)

    def creer(self, titre: str, debut: float, duree_min: int = 10, description: str = "", rappels=(10, 0)):
        cid = self.calendrier_id()
        ms = int(debut * 1000)
        titre = titre_propre(titre)
        desc = (" — ".join(l for l in description.splitlines() if l.strip() and MARQUE not in l) + f" — {MARQUE}")
        desc = desc.replace(":", "∶")
        binds = [f"calendar_id:i:{cid}", f"title:s:{titre}", f"dtstart:l:{ms}",
                 f"dtend:l:{ms + duree_min * 60000}", f"eventTimezone:s:{FUSEAU}",
                 "hasAlarm:i:1", f"description:s:{desc}"]
        _ok(self.adb.shell("content insert --uri content://com.android.calendar/events "
                           + " ".join("--bind " + shlex.quote(b) for b in binds)))
        ids = self._ids(f"calendar_id={cid} AND dtstart={ms} AND description LIKE '%{MARQUE}%' AND deleted=0")
        if not ids:
            raise AgendaErreur("event pas retrouvé après création")
        for m in sorted(set(int(x) for x in rappels)):
            _ok(self.adb.shell("content insert --uri content://com.android.calendar/reminders "
                               f"--bind event_id:l:{ids[-1]} --bind minutes:i:{m} --bind method:i:1"))
