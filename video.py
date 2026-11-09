"""Images de l'écran du téléphone par la vidéo du serveur scrcpy (H.264, décodé avec PyAV),
comme le bot CoC. Une image toute fraîche arrive en ~50 ms, contre ~1,2 s pour
« adb exec-out screencap ». La vidéo se coupe d'elle-même après REPOS_S sans capture."""
from __future__ import annotations

import random
import re
import socket
import struct
import subprocess
import threading
import time

from PIL import Image

from presse_papier import SERVEUR_TEL, envoyer_serveur

try:
    import av
except ImportError:
    av = None

DEBIT = 16_000_000   # bits/s, à la taille réelle de l'écran : le texte reste net pour l'OCR
IPS = 15
REPOS_S = 20.0
LATENCE = 0.05       # entre l'affichage d'une image et son arrivée sur le PC


class VideoEcran:
    def __init__(self, serial: str = ""):
        self.serial = serial
        self.adb = ["adb"] + (["-s", serial] if serial else [])
        self.proc = self.sock = self.port = None
        self.ecran = None            # (petit côté, grand côté) de l'écran, d'après « wm size »
        self.image = None            # dernière av.VideoFrame décodée
        self.t_image = 0.0           # heure (time.time) de son arrivée
        self.demande = 0.0           # heure de la dernière capture demandée
        self.cond = threading.Condition()
        self.verrou = threading.RLock()

    def _adb(self, *args, timeout=30):
        return subprocess.run([*self.adb, *args], capture_output=True, text=True, timeout=timeout)

    def demarrer(self):
        with self.verrou:
            if self.sock is not None:
                return
            if av is None:
                raise RuntimeError("PyAV n'est pas installé (pip install av)")
            ver = envoyer_serveur(self.adb)
            # « Physical size », puis « Override size » si la résolution a été changée
            tailles = re.findall(r"(\d+)x(\d+)", self._adb("shell", "wm size").stdout)
            if tailles:
                w, h = map(int, tailles[-1])
                self.ecran = (min(w, h), max(w, h))
            scid = random.randint(1, 0x7FFFFFFF)
            r = self._adb("forward", "tcp:0", f"localabstract:scrcpy_{scid:08x}")
            if r.returncode != 0 or not r.stdout.strip().isdigit():
                raise RuntimeError("tunnel ADB refusé : " + (r.stderr or r.stdout).strip()[:120])
            self.port = int(r.stdout)
            cmd = (f"CLASSPATH={SERVEUR_TEL} app_process / com.genymobile.scrcpy.Server {ver} "
                   f"scid={scid:08x} log_level=warn video=true audio=false control=false "
                   f"video_codec=h264 max_size=0 max_fps={IPS} video_bit_rate={DEBIT} "
                   "tunnel_forward=true send_device_meta=false send_dummy_byte=true "
                   "power_on=false cleanup=false")
            self.proc = subprocess.Popen([*self.adb, "shell", cmd], stdout=subprocess.DEVNULL,
                                         stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
            fin = time.time() + 10
            while self.sock is None and time.time() < fin:
                if self.proc.poll() is not None:
                    err = (self.proc.stderr.read() or b"").decode(errors="replace").strip()[-300:]
                    self.arreter()
                    raise RuntimeError("serveur scrcpy arrêté : " + err)
                s = None
                try:
                    s = socket.create_connection(("127.0.0.1", self.port), timeout=2)
                    s.settimeout(2)
                    if s.recv(1) == b"\x00":   # octet « factice » : le serveur est vraiment là
                        self.sock, s = s, None
                except OSError:
                    pass
                finally:
                    if s is not None:
                        s.close()
                if self.sock is None:
                    time.sleep(0.2)
            if self.sock is None:
                self.arreter()
                raise RuntimeError("connexion au serveur scrcpy impossible")
            self.sock.settimeout(1.0)
            threading.Thread(target=self._recevoir, args=(self.sock,), daemon=True).start()

    def _lire(self, sock, n):
        tampon = bytearray()
        while len(tampon) < n:
            try:
                morceau = sock.recv(n - len(tampon))
            except TimeoutError:             # écran figé ou éteint : rien n'arrive
                if time.time() - self.demande > REPOS_S:
                    raise EOFError("au repos")
                continue
            if not morceau:
                raise EOFError("vidéo coupée")
            tampon += morceau
        return bytes(tampon)

    def _recevoir(self, sock):
        """Chaque paquet a un en-tête de 12 octets : bit 63 = paquet de session (taille de la
        vidéo, sans contenu), bit 62 = configuration (SPS/PPS, à coller au paquet suivant),
        sinon une image ; les 4 derniers octets donnent la taille du contenu."""
        config = b""
        try:
            if self._lire(sock, 4) != b"h264":
                raise ValueError("codec inattendu")
            decodeur = av.CodecContext.create("h264", "r")
            while time.time() - self.demande < REPOS_S:
                entete = self._lire(sock, 12)
                if entete[0] & 0x80:
                    continue
                donnees = self._lire(sock, struct.unpack(">I", entete[8:])[0])
                if entete[0] & 0x40:
                    config = donnees
                    continue
                for image in decodeur.decode(av.Packet(config + donnees)):
                    with self.cond:
                        self.image, self.t_image = image, time.time()
                        self.cond.notify_all()
                config = b""
        except (OSError, EOFError, ValueError, struct.error, av.error.FFmpegError):
            pass
        self.arreter(sock)

    def capture(self, delai=1.5):
        """(image RGB à la taille de l'écran, heure où elle était affichée), ou None si
        aucune image neuve n'arrive à temps (écran éteint…)."""
        t = self.demande = time.time()
        self.demarrer()
        fin = time.time() + delai
        with self.cond:
            while self.t_image <= t and self.sock is not None and time.time() < fin:
                self.cond.wait(fin - time.time())
            if self.t_image <= t:
                return None
            image, t_image = self.image, self.t_image
        img = image.to_image()
        if self.ecran:
            petit, grand = self.ecran
            taille = (grand, petit) if img.width > img.height else (petit, grand)
            if img.size != taille:           # le serveur arrondit la vidéo à un multiple de 8
                img = img.resize(taille, Image.BICUBIC)
        return img, t_image - LATENCE

    def arreter(self, sock=None):
        """Coupe la vidéo ; avec sock, seulement si c'est encore la connexion en cours."""
        with self.verrou:
            if sock is not None and sock is not self.sock:
                return
            if self.sock is not None:
                try:
                    self.sock.close()
                except OSError:
                    pass
                self.sock = None
            if self.proc is not None:
                if self.proc.poll() is None:
                    self.proc.terminate()
                    try:
                        self.proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self.proc.kill()
                self.proc = None
            if self.port:
                try:
                    self._adb("forward", "--remove", f"tcp:{self.port}", timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    pass
                self.port = None
        with self.cond:
            self.cond.notify_all()
