#!/usr/bin/env bash
# Installe CoC Alarmes dans son dossier (environnement Python à part) et ajoute un lanceur au menu.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
ICI="$PWD"

command -v adb >/dev/null || echo "⚠ adb introuvable : installe android-tools (Arch) ou adb (Debian/Ubuntu)."

python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
chmod +x coc-alarmes

mkdir -p ~/.local/share/applications
cat > ~/.local/share/applications/coc-alarmes.desktop <<DESK
[Desktop Entry]
Type=Application
Name=CoC Alarmes
Comment=Fins d'améliorations Clash of Clans -> alarmes et agenda
Exec=$ICI/coc-alarmes
Icon=$ICI/ui/icone.png
Categories=Utility;Game;
DESK
echo "✔ Installé. Lance « CoC Alarmes » depuis le menu, ou : $ICI/coc-alarmes"
