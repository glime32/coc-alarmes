#!/usr/bin/env python3
"""Teste video.py sans téléphone : ffmpeg fabrique une vidéo H.264 à la taille qu'enverrait
le serveur scrcpy pour un écran 2340×1080 (arrondie à 2336), un faux serveur l'envoie avec les
en-têtes de scrcpy 5, et on vérifie les captures. Il faut ffmpeg (libx264) et PyAV."""
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import video

W, H, IPS = 2336, 1080, 15
resultats = []


def verifie(nom, ok, detail=""):
    resultats.append(bool(ok))
    print(f"  {'ok   ' if ok else 'ÉCHEC'} {nom}{'' if ok or not detail else ' : ' + detail}")


def nal_units(flux):
    debuts = []
    i = flux.find(b"\x00\x00\x01")
    while i >= 0:
        debuts.append(i + 3)
        i = flux.find(b"\x00\x00\x01", i + 3)
    for k, d in enumerate(debuts):
        fin = debuts[k + 1] - 3 if k + 1 < len(debuts) else len(flux)
        yield b"\x00\x00\x00\x01" + flux[d:fin].rstrip(b"\x00")


def paquets(flux):
    """(config SPS+PPS, [(image clé ?, données)]) comme les découpe MediaCodec."""
    config, images, avant = b"", [], b""
    for nal in nal_units(flux):
        genre = nal[4] & 0x1F
        if genre in (7, 8):
            config += nal
        elif genre in (1, 5):
            images.append((genre == 5, avant + nal))
            avant = b""
        else:
            avant += nal
    return config, images


def serveur(sock, config, images, envoyees):
    sock.sendall(b"h264" + struct.pack(">QI", 1 << 63, 0)[:4] + struct.pack(">II", W, H))
    sock.sendall(struct.pack(">QI", 1 << 62, len(config)) + config)
    for i, (cle, donnees) in enumerate(images):
        sock.sendall(struct.pack(">QI", (1 << 61 if cle else 0) | i * 1_000_000 // IPS, len(donnees)) + donnees)
        envoyees.append(time.time())
        time.sleep(1 / IPS)


with tempfile.TemporaryDirectory() as tmp:
    fichier = os.path.join(tmp, "ecran.h264")
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate={IPS}",
                    "-t", "1.5", "-c:v", "libx264", "-bf", "0", "-g", str(IPS), "-threads", "1",
                    "-pix_fmt", "yuv420p", "-f", "h264", fichier], check=True)
    config, images = paquets(open(fichier, "rb").read())
verifie(f"vidéo découpée comme MediaCodec ({len(images)} images)", config and len(images) >= 20)

a, b = socket.socketpair()
a.settimeout(1.0)
v = video.VideoEcran()
v.ecran = (1080, 2340)
v.sock, v.demande = a, time.time()
envoyees = []
recepteur = threading.Thread(target=v._recevoir, args=(a,))
recepteur.start()
threading.Thread(target=serveur, args=(b, config, images, envoyees), daemon=True).start()

r = v.capture()
verifie("première capture", r is not None)
img, t = r
verifie("image remise à la taille de l'écran (2340×1080)", img.size == (2340, 1080), str(img.size))
couleurs = img.resize((32, 16)).getcolors(512)
verifie("image décodée (mire en couleurs, pas un écran uni)", couleurs is None or len(couleurs) > 20)
verifie("heure de l'image juste avant l'arrivée", 0 < time.time() - t < 0.5, f"{time.time() - t:.3f} s")

t0 = time.time()
r2 = v.capture()
verifie("capture suivante : une image arrivée après la demande", r2 is not None and r2[1] + video.LATENCE > t0)
verifie("et en moins de 2 images", time.time() - t0 < 2.5 / IPS, f"{time.time() - t0:.3f} s")

while len(envoyees) < len(images):
    time.sleep(0.05)
time.sleep(0.2)
verifie("plus rien n'arrive : None (et pas une vieille image)", v.capture(delai=0.3) is None)

video.REPOS_S = 0.5
time.sleep(0.6)
recepteur.join(timeout=3)
verifie("au repos : la vidéo se coupe d'elle-même", not recepteur.is_alive() and v.sock is None)
b.close()

print(f"{sum(resultats)} / {len(resultats)}")
sys.exit(0 if all(resultats) else 1)
