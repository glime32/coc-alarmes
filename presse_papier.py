"""Lit le presse-papier du téléphone directement, via le serveur de scrcpy (sans fenêtre, sans vidéo).
Pourquoi : sous Wayland, scrcpy ne peut pas écrire dans le presse-papier du PC si sa fenêtre
n'est pas active. Ici on parle au serveur scrcpy du téléphone et on reçoit le texte nous-mêmes."""
from __future__ import annotations

import os
import random
import re
import socket
import struct
import subprocess
import threading
import time

SERVEUR_PC = "/usr/share/scrcpy/scrcpy-server"
SERVEUR_TEL = "/data/local/tmp/coc-alarmes-scrcpy-server.jar"
GET_CLIPBOARD = 8
DEV_CLIPBOARD, DEV_ACK, DEV_UHID = 0, 1, 2


def version_scrcpy() -> str:
    out = subprocess.run(["scrcpy", "--version"], capture_output=True, text=True).stdout
    m = re.search(r"scrcpy (\d+\.\d+(?:\.\d+)?)", out)
    if not m:
        raise RuntimeError("version de scrcpy introuvable")
    return m.group(1)


class PontPressePapier:
    def __init__(self, serial: str, quand_change=None):
        self.serial = serial
        self.quand_change = quand_change   # fonction(texte) appelée à chaque copie sur le téléphone
        self.sock = None
        self.proc = None
        self.port = None
        self.scid = None
        self.dernier = None
        self._attente = threading.Event()
        self._fil = None
        self.actif = False

    def _adb(self, *args, **kw):
        return subprocess.run(["adb", "-s", self.serial, *args], capture_output=True, text=True, timeout=30, **kw)

    def demarrer(self):
        if self.actif:
            return
        ver = version_scrcpy()
        taille = os.path.getsize(SERVEUR_PC)
        st = self._adb("shell", f"stat -c %s {SERVEUR_TEL} 2>/dev/null").stdout.strip()
        if st != str(taille):
            r = self._adb("push", SERVEUR_PC, SERVEUR_TEL)
            if r.returncode != 0:
                raise RuntimeError("envoi du serveur scrcpy raté : " + r.stderr.strip())
        self.scid = random.randint(1, 0x7FFFFFFF)
        nom = f"scrcpy_{self.scid:08x}"
        r = self._adb("forward", "tcp:0", f"localabstract:{nom}")
        self.port = int(r.stdout.strip())
        cmd = (f"CLASSPATH={SERVEUR_TEL} app_process / com.genymobile.scrcpy.Server {ver} "
               f"scid={self.scid:08x} log_level=warn video=false audio=false control=true "
               "tunnel_forward=true send_device_meta=false send_dummy_byte=true "
               "clipboard_autosync=true power_on=false cleanup=false")
        self.proc = subprocess.Popen(["adb", "-s", self.serial, "shell", cmd],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        fin = time.time() + 10
        while time.time() < fin:
            if self.proc.poll() is not None:
                raise RuntimeError("serveur scrcpy arrêté : " + (self.proc.stderr.read() or b"").decode()[-300:])
            try:
                s = socket.create_connection(("127.0.0.1", self.port), timeout=2)
                s.settimeout(2)
                if s.recv(1) == b"\x00":   # octet « factice » = le serveur est vraiment là
                    s.settimeout(None)
                    self.sock = s
                    break
                s.close()
            except OSError:
                pass
            time.sleep(0.3)
        if self.sock is None:
            self.arreter()
            raise RuntimeError("connexion au serveur scrcpy impossible")
        self.actif = True
        self._fil = threading.Thread(target=self._lire, daemon=True)
        self._fil.start()

    def _recv(self, n):
        b = b""
        while len(b) < n:
            c = self.sock.recv(n - len(b))
            if not c:
                raise ConnectionError("fermé")
            b += c
        return b

    def _lire(self):
        try:
            while True:
                t = self._recv(1)[0]
                if t == DEV_CLIPBOARD:
                    n = struct.unpack(">I", self._recv(4))[0]
                    texte = self._recv(n).decode("utf-8", "replace")
                    self.dernier = texte
                    self._attente.set()
                    if self.quand_change:
                        try:
                            self.quand_change(texte)
                        except Exception:
                            pass
                elif t == DEV_ACK:
                    self._recv(8)
                elif t == DEV_UHID:
                    _id, n = struct.unpack(">HH", self._recv(4))
                    self._recv(n)
                else:
                    break
        except Exception:
            pass
        self.actif = False

    def lire(self, timeout=5.0) -> str | None:
        """Demande le presse-papier actuel du téléphone."""
        if not self.actif:
            self.demarrer()
        self._attente.clear()
        self.sock.sendall(bytes([GET_CLIPBOARD, 0]))
        if self._attente.wait(timeout):
            return self.dernier
        return None

    def arreter(self):
        self.actif = False
        try:
            if self.sock:
                self.sock.close()
        except OSError:
            pass
        self.sock = None
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        if self.port:
            self._adb("forward", "--remove", f"tcp:{self.port}")
            self.port = None
