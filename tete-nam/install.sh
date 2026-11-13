#!/bin/bash
# Installation de la Tête NAM sur Raspberry Pi 5 (Raspberry Pi OS Lite 64 bits,
# Bookworm ou Trixie). Moteur audio : stompbox (NAM + effets compilés).
#
# Usage, depuis le dossier du dépôt stompbox cloné :
#   sudo ./tete-nam/install.sh
# Options :
#   --sans-ecran     pas d'interface sur l'écran de la tête (page web et appli seulement)
#   --ecran-dsi0     écran branché sur le connecteur DSI 0 (CAM/DISP 0) au lieu du DSI 1
#   --generique      compile sans optimisation propre au processeur du Pi 5
#
# Relancer le script après une mise à jour du dépôt (git pull) : il ne
# recompile que ce qui a changé et garde réglages, presets et modèles.
set -euo pipefail

ECRAN=oui
PORT_DSI=1
OPTIM=oui
for a in "$@"; do
    case "$a" in
        --sans-ecran) ECRAN=non ;;
        --ecran-dsi0) PORT_DSI=0 ;;
        --generique) OPTIM=non ;;
        *) echo "Option inconnue : $a"; exit 1 ;;
    esac
done

etape() { echo; echo "=== $* ==="; }

if [ "$(id -u)" -ne 0 ]; then
    echo "Lancez ce script avec sudo :  sudo ./tete-nam/install.sh"; exit 1
fi
UTIL="${SUDO_USER:-}"
if [ -z "$UTIL" ] || [ "$UTIL" = root ]; then
    echo "Lancez ce script avec sudo depuis votre utilisateur normal (pas root)"; exit 1
fi
MAISON=$(getent passwd "$UTIL" | cut -d: -f6)
DEPOT=$(cd "$(dirname "$0")/.." && pwd)
ICI="$DEPOT/tete-nam"
CIBLE=/opt/tete-nam
MODELE=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo "inconnu")

etape "1/8 Vérifications"
echo "Carte : $MODELE"
[ "$(uname -m)" = aarch64 ] || { echo "ERREUR : système non 64 bits ($(uname -m)). Il faut Raspberry Pi OS Lite 64 bits."; exit 1; }
case "$MODELE" in
    *"Raspberry Pi 5"*|*"Compute Module 5"*) PI5=oui ;;
    *) PI5=non; echo "ATTENTION : prévu pour Raspberry Pi 5 / CM5, l'installation continue quand même" ;;
esac
[ -f "$DEPOT/stompbox-jack/stompbox-jack.cpp" ] || { echo "ERREUR : lancez le script depuis le dépôt stompbox"; exit 1; }

etape "2/8 Paquets (JACK, compilation, Bluetooth, écran)"
echo "jackd2 jackd/tweak_rt_limits boolean true" | debconf-set-selections
apt-get update
PAQUETS="git build-essential cmake pkg-config python3 curl jackd2 libjack-jackd2-dev
         bluez python3-dbus python3-gi rfkill"
if [ "$ECRAN" = oui ]; then
    # Le nom du paquet Chromium dépend de la version de Raspberry Pi OS
    if apt-cache policy chromium 2>/dev/null | grep -q 'Candidate: [0-9]'; then NAV=chromium
    else NAV=chromium-browser; fi
    PAQUETS="$PAQUETS cage $NAV wlr-randr fonts-dejavu-core plymouth"
fi
# shellcheck disable=SC2086
DEBIAN_FRONTEND=noninteractive apt-get install -y $PAQUETS
# Texte du pourcentage sur le logo de démarrage (facultatif)
[ "$ECRAN" = oui ] && { DEBIAN_FRONTEND=noninteractive apt-get install -y plymouth-label || true; }
# Police du thème de l'interface (facultative : sinon police par défaut)
[ "$ECRAN" = oui ] && { DEBIAN_FRONTEND=noninteractive apt-get install -y fonts-montserrat || true; }

etape "3/8 Compilation du moteur audio stompbox-jack (5 à 10 min la première fois)"
sudo -u "$UTIL" git -C "$DEPOT" submodule update --init --recursive
CONSTR="$DEPOT/build-tete-nam"
DRAPEAUX=""
if [ "$OPTIM" = oui ] && [ "$PI5" = oui ]; then
    # Pi 5 et CM5 : processeur Cortex-A76 (instructions NEON plus récentes)
    DRAPEAUX="-mcpu=cortex-a76"
fi
# Compilation en parallèle selon la mémoire : environ 1,2 Go par tâche
MEMOIRE_MO=$(awk '/MemTotal/ {print int($2 / 1024)}' /proc/meminfo)
TACHES=$(( MEMOIRE_MO / 1200 )); [ "$TACHES" -ge 1 ] || TACHES=1
[ "$TACHES" -le "$(nproc)" ] || TACHES=$(nproc)
echo "Compilation avec $TACHES tâches en parallèle ($MEMOIRE_MO Mo de mémoire)"
sudo -u "$UTIL" cmake -S "$DEPOT" -B "$CONSTR" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_FLAGS="$DRAPEAUX" -DCMAKE_CXX_FLAGS="$DRAPEAUX" >/dev/null
sudo -u "$UTIL" cmake --build "$CONSTR" --target stompbox-jack -j"$TACHES"
BINAIRE="$CONSTR/stompbox-jack/stompbox-jack"
[ -x "$BINAIRE" ] || { echo "ERREUR : la compilation n'a pas produit stompbox-jack"; exit 1; }

etape "4/8 Fichiers de la Tête NAM ($CIBLE)"
# On repart d'un dossier propre (l'ancienne version Pi 3 / mod-host y laissait
# d'autres fichiers). Réglages, presets et modèles sont ailleurs : rien n'est perdu.
rm -rf "$CIBLE"
install -d "$CIBLE"
install -m 755 "$BINAIRE" "$CIBLE/stompbox-jack"
install -m 755 "$ICI/run.sh" "$ICI/ecran.sh" "$ICI/cpu-performance.sh" "$ICI/tetenam.py" \
    "$ICI/web.py" "$ICI/moteur.py" "$ICI/midi.py" "$ICI/ble.py" "$ICI/wifi-secours.sh" \
    "$ICI/tone3000.py" "$ICI/bibliotheque.py" "$CIBLE/"
install -m 644 "$ICI/ui.html" "$ICI/attente.html" "$ICI/chaine-init.txt" "$ICI/VERSION" "$ICI/demarrage/logo.png" \
    "$ICI/tone3000-logo.svg" "$ICI/tone3000-marque.svg" "$CIBLE/"
echo "$(cat "$ICI/VERSION") ($(git -C "$DEPOT" rev-parse --short HEAD 2>/dev/null || echo '?'))" > "$CIBLE/VERSION"
ln -sf "$CIBLE/tetenam.py" /usr/local/bin/tete-nam
ln -sf "$CIBLE/wifi-secours.sh" /usr/local/bin/tete-nam-wifi
if [ ! -e /etc/tete-nam-wifi.conf ]; then
    install -m 644 "$ICI/tete-nam-wifi.conf" /etc/tete-nam-wifi.conf
    echo "Réglages Wi-Fi de secours créés : /etc/tete-nam-wifi.conf"
fi

etape "5/8 Réglages, presets et modèles"
# Modèles .nam : ~/nam ; dossier de travail du moteur : ~/tete-nam
sudo -u "$UTIL" mkdir -p "$MAISON/nam" "$MAISON/tete-nam/Presets" "$MAISON/tete-nam/Cabinets" \
    "$MAISON/tete-nam/Reverb" "$MAISON/tete-nam/Music"
if [ ! -L "$MAISON/tete-nam/NAM" ]; then
    rm -rf "$MAISON/tete-nam/NAM"
    sudo -u "$UTIL" ln -s "$MAISON/nam" "$MAISON/tete-nam/NAM"
fi
if [ -e /etc/tete-nam.conf ]; then
    cp /etc/tete-nam.conf /etc/tete-nam.conf.avant-stompbox-"$(date +%Y%m%d-%H%M%S)"
    echo "Réglages existants gardés (copie de sécurité dans /etc)"
else
    touch /etc/tete-nam.conf
fi
# La page web enregistre les réglages dans ce fichier : il est à l'utilisateur
chown "$UTIL" /etc/tete-nam.conf
chmod 644 /etc/tete-nam.conf
# Remet le fichier au format actuel (et reprend la chaîne Pédale -> Ampli de
# l'ancienne version, s'il y en avait une)
sudo -u "$UTIL" -H env TETE_NAM_CONF=/etc/tete-nam.conf python3 "$CIBLE/tetenam.py" migrer
if [ "$ECRAN" = non ]; then
    sed -i 's/^ECRAN=.*/ECRAN=non/' /etc/tete-nam.conf
fi
# La page web relance le service audio quand on change le buffer, et choisit
# le réseau Wi-Fi (page Système) avec tete-nam-wifi
cat > /etc/sudoers.d/tete-nam <<EOF
$UTIL ALL=(root) NOPASSWD: /usr/bin/systemctl restart --no-block tete-nam
$UTIL ALL=(root) NOPASSWD: /usr/local/bin/tete-nam-wifi
EOF
chmod 440 /etc/sudoers.d/tete-nam
visudo -cf /etc/sudoers.d/tete-nam >/dev/null \
    || { rm -f /etc/sudoers.d/tete-nam; echo "ERREUR : règle sudo refusée"; exit 1; }

etape "6/8 Réglages temps réel"
usermod -aG audio,video,render,input,dialout,systemd-journal "$UTIL"
cat > /etc/security/limits.d/95-tete-nam.conf <<EOF
@audio - rtprio 95
@audio - memlock unlimited
EOF
# threadirqs : meilleure priorité pour l'USB audio
# usbcore.autosuspend=-1 : pas de mise en veille de la carte son USB
CMDLINE=/boot/firmware/cmdline.txt
[ -e "$CMDLINE" ] || CMDLINE=/boot/cmdline.txt
[ -e "$CMDLINE.avant-tete-nam" ] || cp "$CMDLINE" "$CMDLINE.avant-tete-nam"
for opt in threadirqs usbcore.autosuspend=-1; do
    grep -qw -- "$opt" "$CMDLINE" || sed -i "1 s/\$/ $opt/" "$CMDLINE"
done

etape "7/8 Écran tactile DSI"
CONFIG=/boot/firmware/config.txt
[ -e "$CONFIG" ] || CONFIG=/boot/config.txt
if [ "$ECRAN" = oui ]; then
    [ -e "$CONFIG.avant-tete-nam" ] || cp "$CONFIG" "$CONFIG.avant-tete-nam"
    # Bloc à nous, entre deux marques : remplacé à chaque installation
    sed -i '/^# --- Tete NAM debut ---$/,/^# --- Tete NAM fin ---$/d' "$CONFIG"
    OVERLAY=""
    if grep -v '^[[:space:]]*#' "$CONFIG" | grep -q 'dsi'; then
        echo "Une ligne d'écran DSI existe déjà dans $CONFIG : gardée telle quelle"
    else
        OVERLAY="dtoverlay=vc4-kms-dsi-7inch"
        [ "$PORT_DSI" = 0 ] && OVERLAY="$OVERLAY,dsi0"
        echo "Écran DSI $PORT_DSI ajouté dans $CONFIG ($OVERLAY)"
    fi
    cat >> "$CONFIG" <<EOF
# --- Tete NAM debut ---
[all]
# Écran tactile 7" DSI 800x480 (compatible écran officiel v1)
$OVERLAY
# Pas d'écran arc-en-ciel du firmware au démarrage
disable_splash=1
# Démarrage plus rapide : pas de pause avant Linux, pas de recherche de
# caméra ni d'écran officiel (l'écran est déclaré juste au-dessus)
boot_delay=0
camera_auto_detect=0
display_auto_detect=0
# --- Tete NAM fin ---
EOF

    # Démarrage sans texte : logo et barre de progression (Plymouth), console
    # sans curseur clignotant, messages du noyau masqués
    for opt in quiet splash loglevel=3 logo.nologo vt.global_cursor_default=0 plymouth.ignore-serial-consoles; do
        grep -qw -- "$opt" "$CMDLINE" || sed -i "1 s/\$/ $opt/" "$CMDLINE"
    done
    THEME=/usr/share/plymouth/themes/tete-nam
    mkdir -p "$THEME"
    AVANT=$( (cat "$THEME"/* 2>/dev/null || true) | md5sum)
    install -m 644 "$ICI/demarrage/tete-nam.plymouth" "$ICI/demarrage/tete-nam.script" \
        "$ICI/demarrage/logo.png" "$ICI/demarrage/barre.png" "$ICI/demarrage/barre-fond.png" "$THEME/"
    if [ "$(plymouth-set-default-theme 2>/dev/null)" != tete-nam ] || [ "$AVANT" != "$(cat "$THEME"/* | md5sum)" ]; then
        echo "Logo de démarrage installé, mise à jour de l'initramfs (une minute)..."
        plymouth-set-default-theme tete-nam
        update-initramfs -u || echo "ATTENTION : initramfs non mis à jour (le logo apparaîtra un peu plus tard au démarrage)"
    fi
    # Curseur invisible sur l'écran tactile
    # Sans pointeur déclaré, cage n'affiche pas de flèche (voir le fichier)
    install -m 644 "$ICI/demarrage/99-tete-nam-sans-curseur.rules" /etc/udev/rules.d/
    udevadm control --reload-rules 2>/dev/null || true
    python3 "$ICI/demarrage/curseur-invisible.py" /usr/share/icons/tete-nam-invisible
    # cage charge le thème de curseur « default » de l'utilisateur : on le fait
    # pointer vers le thème invisible (seulement pour cet utilisateur)
    sudo -u "$UTIL" mkdir -p "$MAISON/.icons/default"
    sudo -u "$UTIL" ln -sfn /usr/share/icons/tete-nam-invisible/cursors "$MAISON/.icons/default/cursors"
    printf '[Icon Theme]\nName=default\nInherits=tete-nam-invisible\n' | sudo -u "$UTIL" tee "$MAISON/.icons/default/index.theme" >/dev/null
    # Chromium : jamais de proposition de traduction (la page est en français),
    # et thème noir : pas de flash blanc avant que la page s'affiche
    for d in /etc/chromium/policies/managed /etc/chromium-browser/policies/managed; do
        mkdir -p "$d"
        echo '{"TranslateEnabled": false, "BrowserThemeColor": "#000000"}' > "$d/tete-nam.json"
    done
    # La console texte ne s'affiche plus sur l'écran (Ctrl+Alt+F2 en garde une)
    systemctl disable getty@tty1.service 2>/dev/null || true
    # Session de connexion de l'écran (accès à l'affichage et au tactile)
    cat > /etc/pam.d/tete-nam-ecran <<EOF
auth     required pam_unix.so nullok
account  required pam_unix.so
session  required pam_unix.so
session  required pam_systemd.so
EOF
else
    echo "Pas d'écran (--sans-ecran)"
    systemctl enable getty@tty1.service 2>/dev/null || true
fi

etape "8/8 Démarrage automatique (et rapide)"
# Services de Raspberry Pi OS inutiles sur la tête : coupés pour démarrer plus
# vite. Liste gardée dans /etc/tete-nam-services-coupes.txt ; pour en remettre
# un : sudo systemctl enable NOM
#   NetworkManager-wait-online : attente du réseau (la tête n'en a pas besoin)
#   ModemManager : modems 3G/4G ; triggerhappy : touches multimédia
#   apt-daily*, man-db : mises à jour et index en tâche de fond
#   rpi-eeprom-update : vérif. du firmware à chaque démarrage (se fait à la main)
#   e2scrub_reap, glamor-test : sans utilité ici
COUPES=""
for svc in NetworkManager-wait-online.service ModemManager.service triggerhappy.service \
           triggerhappy.socket apt-daily.timer apt-daily-upgrade.timer man-db.timer \
           rpi-eeprom-update.service e2scrub_reap.service glamor-test.service; do
    if systemctl is-enabled -q "$svc" 2>/dev/null; then
        systemctl disable "$svc" 2>/dev/null && COUPES="$COUPES $svc"
    fi
done
# cloud-init (Raspberry Pi OS Trixie) : ne sert qu'au tout premier démarrage,
# déjà passé ; il ralentit chacun des suivants
if [ -d /etc/cloud ] && [ ! -e /etc/cloud/cloud-init.disabled ]; then
    touch /etc/cloud/cloud-init.disabled
    COUPES="$COUPES cloud-init(/etc/cloud/cloud-init.disabled)"
fi
if [ -n "$COUPES" ]; then
    echo "Services coupés pour démarrer plus vite :$COUPES"
    echo "$COUPES" | tr ' ' '\n' | sed '/^$/d' >> /etc/tete-nam-services-coupes.txt
fi

for svc in tete-nam tete-nam-web tete-nam-ecran; do
    sed "s/__USER__/$UTIL/" "$ICI/$svc.service" > "/etc/systemd/system/$svc.service"
done
install -m 644 "$ICI/tete-nam-wifi.service" "$ICI/tete-nam-ble.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable tete-nam.service tete-nam-web.service tete-nam-wifi.service tete-nam-ble.service
if [ "$ECRAN" = oui ]; then
    systemctl enable tete-nam-ecran.service
else
    systemctl disable tete-nam-ecran.service 2>/dev/null || true
fi
# Bluetooth pour l'appli Android (le Wi-Fi reste disponible)
rfkill unblock bluetooth 2>/dev/null || true
systemctl enable --now bluetooth.service 2>/dev/null || true
if grep -q '^dtoverlay=disable-bt' "$CONFIG"; then
    echo "ATTENTION : $CONFIG contient dtoverlay=disable-bt, le Bluetooth est coupé."
    echo "Mettez un # devant cette ligne puis redémarrez pour utiliser l'appli."
fi
# Si la tête tournait déjà (mise à jour), on relance avec la nouvelle version.
# L'écran, lui, démarre au prochain redémarrage (il prend la place de la
# console sur l'écran : pas pendant qu'on y tape peut-être cette installation).
if systemctl is-active -q tete-nam.service; then systemctl restart tete-nam.service; fi
systemctl restart tete-nam-web.service || true
systemctl restart tete-nam-ble.service || true

echo
echo "Installation terminée ($(cat "$CIBLE/VERSION"))."
echo "1. Copiez vos modèles .nam dans $MAISON/nam/ (ou envoyez-les depuis la page web"
echo "   ou l'appli)"
echo "2. Redémarrez :  sudo reboot"
echo "3. L'interface s'affiche sur l'écran de la tête. Aussi dans un navigateur :"
echo "   http://$(hostname).local:8080  et dans l'appli Android (Bluetooth, nom TeteNAM)"
echo "4. Vérifiez   :  tete-nam etat"
echo "Sans Wi-Fi connu, la tête crée son propre réseau : voir /etc/tete-nam-wifi.conf"
echo "(nom et mot de passe à changer) et tete-nam-wifi etat"
