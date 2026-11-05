#!/bin/bash
# Lancé par le service "tete-nam-ecran" : l'interface tactile en plein écran
# sur l'écran DSI de la tête. cage est un affichage Wayland minimal qui ne
# montre qu'un seul programme ; ici le navigateur Chromium en mode kiosque,
# sur la page servie par web.py.
set -u
source /etc/tete-nam.conf
if [ "${ECRAN:-oui}" = non ]; then
    echo "Écran désactivé dans /etc/tete-nam.conf (ECRAN=non)"
    exec sleep infinity
fi

URL="http://127.0.0.1:8080/?ecran=1"
NAV=$(command -v chromium || command -v chromium-browser)
[ -n "$NAV" ] || { echo "ERREUR : Chromium n'est pas installé"; exit 1; }

# On attend la page web (au démarrage elle arrive quelques secondes après).
# Le service lance d'abord « ecran.sh --attendre » : le logo de démarrage
# reste affiché pendant ce temps.
for i in $(seq 1 60); do
    curl -s -o /dev/null "http://127.0.0.1:8080/api/etat" && break
    sleep 1
done
[ "${1:-}" = --attendre ] && exit 0

# Écran tactile : curseur invisible (thème installé par install.sh)
if [ -d /usr/share/icons/tete-nam-invisible ]; then
    export XCURSOR_THEME=tete-nam-invisible XCURSOR_SIZE=24
fi

# Profil jetable : rien ne s'accumule sur la carte SD, pas de « restaurer la session »
PROFIL="${XDG_RUNTIME_DIR:-/tmp}/tete-nam-chromium"
rm -rf "$PROFIL"

# Rotation éventuelle, appliquée dans cage avant de lancer le navigateur
LANCER="exec \"$NAV\" --kiosk --app=\"$URL\" --ozone-platform=wayland --user-data-dir=\"$PROFIL\" \
 --noerrdialogs --disable-infobars --no-first-run --disable-translate --disable-features=Translate \
 --disable-pinch --overscroll-history-navigation=0 --disable-session-crashed-bubble \
 --check-for-update-interval=31536000 --password-store=basic --autoplay-policy=no-user-gesture-required \
 --disk-cache-size=1 --enable-features=OverlayScrollbar"
if [ "${ECRAN_ROTATION:-0}" != 0 ] && command -v wlr-randr >/dev/null; then
    LANCER="for s in \$(wlr-randr | awk '/^[^ ]/{print \$1}'); do wlr-randr --output \$s --transform $ECRAN_ROTATION; done; $LANCER"
fi

# -d : pas de bordures de fenêtre ; -s : autorise Ctrl+Alt+F2 pour une console
exec cage -d -s -- /bin/sh -c "$LANCER"
