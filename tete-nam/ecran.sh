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

# Ancienne version du service : il lançait d'abord « ecran.sh --attendre ».
# On n'attend plus la page web : le navigateur démarre tout de suite sur une
# page d'attente locale (attente.html, même aspect que le logo de démarrage),
# qui bascule sur l'interface dès que la page web répond. Le navigateur se
# charge donc PENDANT que le moteur audio et la page web démarrent.
[ "${1:-}" = --attendre ] && exit 0
ATTENTE="file:///opt/tete-nam/attente.html#$URL"
[ -e /opt/tete-nam/attente.html ] || ATTENTE="$URL"

# Navigateur : Chromium (par défaut) ou cog (WPE WebKit, plus léger, à essayer :
# sudo apt install cog, puis ECRAN_NAVIGATEUR=cog dans /etc/tete-nam.conf)
NAVIGATEUR="${ECRAN_NAVIGATEUR:-chromium}"
if [ "$NAVIGATEUR" = cog ] && ! command -v cog >/dev/null; then
    echo "cog n'est pas installé (sudo apt install cog) : Chromium à la place"
    NAVIGATEUR=chromium
fi
NAV=$(command -v chromium || command -v chromium-browser || true)
[ "$NAVIGATEUR" = cog ] || [ -n "$NAV" ] || { echo "ERREUR : Chromium n'est pas installé"; exit 1; }

# Écran tactile : curseur invisible (thème installé par install.sh)
if [ -d /usr/share/icons/tete-nam-invisible ]; then
    export XCURSOR_THEME=tete-nam-invisible XCURSOR_SIZE=24
fi

# Profil jetable : rien ne s'accumule sur la carte SD, pas de « restaurer la session »
PROFIL="${XDG_RUNTIME_DIR:-/tmp}/tete-nam-chromium"
rm -rf "$PROFIL"

# Pas de flash blanc à l'ouverture : avant d'afficher la page, Chromium montre
# une image vide de la couleur de son thème (blanc par défaut). En mode sombre,
# et avec la couleur de thème noire (règle posée par install.sh), elle est
# presque noire.

# Rotation éventuelle, appliquée dans cage avant de lancer le navigateur
LANCER="exec \"$NAV\" --kiosk --app=\"$ATTENTE\" --ozone-platform=wayland --user-data-dir=\"$PROFIL\" \
 --noerrdialogs --disable-infobars --no-first-run --disable-translate --disable-features=Translate,TranslateUI \
 --disable-pinch --overscroll-history-navigation=0 --disable-session-crashed-bubble \
 --check-for-update-interval=31536000 --password-store=basic --autoplay-policy=no-user-gesture-required \
 --disk-cache-size=1 --enable-features=OverlayScrollbar --force-dark-mode --lang=fr --accept-lang=fr \
 --no-default-browser-check --disable-component-update --disable-background-networking --disable-sync \
 --disable-default-apps --disable-extensions"
if [ "$NAVIGATEUR" = cog ]; then
    # cog : un seul onglet plein écran, sans barre ; fond noir avant la page
    LANCER="exec cog -P wl --bg-color=black \"$ATTENTE\""
fi
if [ "${ECRAN_ROTATION:-0}" != 0 ] && command -v wlr-randr >/dev/null; then
    LANCER="for s in \$(wlr-randr | awk '/^[^ ]/{print \$1}'); do wlr-randr --output \$s --transform $ECRAN_ROTATION; done; $LANCER"
fi

# -d : pas de bordures de fenêtre ; -s : autorise Ctrl+Alt+F2 pour une console
exec cage -d -s -- /bin/sh -c "$LANCER"
