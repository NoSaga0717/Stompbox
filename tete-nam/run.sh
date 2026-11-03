#!/bin/bash
# Lancé par le service systemd "tete-nam" : JACK puis le moteur audio stompbox.
# Si l'un des deux s'arrête, on sort et systemd relance le tout.
set -u
source /etc/tete-nam.conf
DONNEES="$HOME/tete-nam"           # presets, chaîne en cours, lien vers les modèles

# Pas de session D-Bus ici : on évite la réservation de carte par D-Bus
export JACK_NO_AUDIO_RESERVATION=1

echo "Attente de la carte $CARD..."
for i in $(seq 1 30); do
    [ -e "/proc/asound/$CARD" ] && break
    sleep 1
done
if [ ! -e "/proc/asound/$CARD" ]; then
    echo "ERREUR : carte $CARD introuvable. Cartes vues :"
    cat /proc/asound/cards
    exit 1
fi

echo "Démarrage de JACK : hw:$CARD, $RATE Hz, buffer $PERIOD x $NPERIODS"
jackd -R -P 80 -d alsa -d "hw:$CARD" -r "$RATE" -p "$PERIOD" -n "$NPERIODS" &
JACK_PID=$!

if ! jack_wait -w -t 15 >/dev/null; then
    echo "ERREUR : JACK n'a pas démarré"
    kill "$JACK_PID" 2>/dev/null
    exit 1
fi

# stompbox travaille dans son dossier courant (Presets/, NAM/, réglages)
mkdir -p "$DONNEES/Presets" "$DONNEES/Cabinets" "$DONNEES/Reverb" "$DONNEES/Music"
cd "$DONNEES" || exit 1

echo "Démarrage du moteur stompbox (entrée $INPUT, sorties $OUTPUTS)"
export JACK_NO_START_SERVER=1
export STOMPBOX_NO_SERIAL_DISPLAY=1
export STOMPBOX_INIT=/opt/tete-nam/chaine-init.txt
export STOMPBOX_INPUT="$INPUT"
export STOMPBOX_OUTPUTS="$OUTPUTS"
/opt/tete-nam/stompbox-jack &
MOTEUR_PID=$!

# La page web (service tete-nam-web) se reconnecte toute seule et recharge la
# chaîne en cours. On reste là tant que JACK et stompbox tournent.
wait -n "$JACK_PID" "$MOTEUR_PID"
echo "JACK ou stompbox s'est arrêté, redémarrage par systemd"
kill "$MOTEUR_PID" "$JACK_PID" 2>/dev/null
exit 1
