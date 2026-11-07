#!/bin/bash
# Wi-Fi de secours de la Tête NAM : si aucun Wi-Fi connu n'est trouvé,
# la tête crée son propre réseau pour joindre la page web depuis un téléphone.
#
#   tete-nam-wifi etat       ce qui est actif en ce moment
#   tete-nam-wifi hotspot    passe sur le réseau de la tête
#   tete-nam-wifi maison     repasse sur le Wi-Fi connu (hotspot si échec)
#   tete-nam-wifi auto       ce que fait le démarrage (service tete-nam-wifi)
#
# Utilisés par la page Système (écran, navigateur, appli) via sudo :
#   tete-nam-wifi chercher           relance une recherche des réseaux
#   tete-nam-wifi connecter <nom>    se connecte (mot de passe lu sur l'entrée
#                                    standard, vide = réseau connu ou ouvert)
#   tete-nam-wifi oublier <nom>      efface le réseau enregistré
# Résultat de la dernière connexion : /run/tete-nam-wifi.json
set -u

PROFIL=tete-nam-hotspot
IFACE=wlan0
# Pas 10.42.0.x (adresses par défaut du partage de connexion de NetworkManager) :
# un réseau câblé qui les utilise entrait en collision avec le hotspot.
IP=192.168.77.1

MODE=auto; ATTENTE=45; SSID=TeteNAM; MOT_DE_PASSE=tetenam2026; CANAL=6
[ -r /etc/tete-nam-wifi.conf ] && source /etc/tete-nam-wifi.conf

log() { echo "tete-nam-wifi : $*"; }

besoin_root() {
    if [ "$(id -u)" -ne 0 ]; then
        exec sudo "$0" "$@"
    fi
}

# Connexion Wi-Fi active sur wlan0 (vide si aucune)
connexion_active() {
    nmcli -t -f GENERAL.STATE,GENERAL.CONNECTION dev show "$IFACE" 2>/dev/null |
        awk -F: '/^GENERAL.STATE/ {etat=$2} /^GENERAL.CONNECTION/ {c=$2}
                 END { if (etat ~ /^100/) print c }'
}

# Connexion câblée active (vide si aucune) : la tête est déjà joignable
cable_actif() {
    nmcli -t -f TYPE,STATE,CONNECTION dev 2>/dev/null |
        awk -F: '$1 == "ethernet" && $2 == "connected" {print $3; exit}'
}

# Crée ou met à jour le profil hotspot à partir des réglages
preparer_profil() {
    if [ ${#MOT_DE_PASSE} -lt 8 ]; then
        log "ERREUR : MOT_DE_PASSE doit faire au moins 8 caractères (/etc/tete-nam-wifi.conf)"
        exit 1
    fi
    nmcli -t -f NAME con show | grep -qx "$PROFIL" ||
        nmcli con add type wifi ifname "$IFACE" con-name "$PROFIL" ssid "$SSID" >/dev/null
    # proto/pairwise/group en CCMP : nécessaire pour que les téléphones
    # récents acceptent le réseau créé par la puce Wi-Fi du Pi.
    nmcli con modify "$PROFIL" \
        connection.autoconnect no \
        802-11-wireless.ssid "$SSID" \
        802-11-wireless.mode ap \
        802-11-wireless.band bg \
        802-11-wireless.channel "$CANAL" \
        802-11-wireless.powersave 2 \
        wifi-sec.key-mgmt wpa-psk \
        wifi-sec.proto rsn \
        wifi-sec.pairwise ccmp \
        wifi-sec.group ccmp \
        wifi-sec.psk "$MOT_DE_PASSE" \
        ipv4.method shared \
        ipv4.addresses "$IP/24" \
        ipv6.method disabled
}

hotspot() {
    preparer_profil
    log "création du réseau « $SSID » (canal $CANAL)"
    if nmcli con up "$PROFIL" >/dev/null; then
        log "OK. Sur le téléphone : Wi-Fi « $SSID », puis http://tetenam.local:8080"
        log "    (ou http://$IP:8080 si le nom ne marche pas)"
    else
        log "ERREUR : le hotspot n'a pas démarré (journalctl -u NetworkManager -n 30)"
        return 1
    fi
}

maison() {
    log "retour au Wi-Fi connu"
    nmcli con down "$PROFIL" >/dev/null 2>&1
    nmcli dev wifi rescan ifname "$IFACE" >/dev/null 2>&1
    sleep 5
    nmcli dev connect "$IFACE" >/dev/null 2>&1
    for _ in $(seq 1 "$ATTENTE"); do
        c=$(connexion_active)
        if [ -n "$c" ] && [ "$c" != "$PROFIL" ]; then
            log "connecté à « $c »"; return 0
        fi
        sleep 1
    done
    log "aucun Wi-Fi connu trouvé : on remet le hotspot pour ne pas perdre la tête"
    hotspot
}

auto() {
    case "$MODE" in
        hotspot) hotspot; return ;;
        maison)  log "MODE=maison : pas de hotspot"; return 0 ;;
    esac
    log "recherche d'un Wi-Fi connu (${ATTENTE} s max)"
    for _ in $(seq 1 "$ATTENTE"); do
        c=$(connexion_active)
        if [ -n "$c" ] && [ "$c" != "$PROFIL" ]; then
            log "connecté à « $c », pas besoin de hotspot"; return 0
        fi
        e=$(cable_actif)
        if [ -n "$e" ]; then
            log "câble réseau branché (« $e »), pas besoin de hotspot"; return 0
        fi
        sleep 1
    done
    log "aucun Wi-Fi connu ni câble réseau"
    hotspot
}

# --- Choix du réseau depuis la page Système --------------------------------

RESULTAT=/run/tete-nam-wifi.json
MDP_TEMP=/run/tete-nam-wifi.mdp

# Écrit le résultat lu par la page : etat (en_cours, ok, echec), réseau, message
resultat() {
    python3 -c 'import json,sys,time; print(json.dumps({"etat": sys.argv[1], "ssid": sys.argv[2], "message": sys.argv[3], "t": time.time()}))' \
        "$1" "$2" "$3" > "$RESULTAT.tmp" && chmod 644 "$RESULTAT.tmp" && mv "$RESULTAT.tmp" "$RESULTAT"
}

# Profils enregistrés pour ce nom de réseau (le nom du profil peut différer du SSID)
profils_de() {
    nmcli -t -f NAME,TYPE con show | while IFS= read -r ligne; do
        type=${ligne##*:}; nom=${ligne%:*}; nom=${nom//\\:/:}
        [ "$type" = "802-11-wireless" ] || continue
        [ "$nom" = "$PROFIL" ] && continue
        [ "$(nmcli -g 802-11-wireless.ssid con show "$nom" 2>/dev/null)" = "$1" ] && echo "$nom"
    done
}

# La puce du Pi ne cherche pas les réseaux pendant qu'elle fait hotspot :
# on coupe le hotspot le temps de la recherche (quelques secondes).
chercher() {
    if [ "$(connexion_active)" = "$PROFIL" ]; then
        nmcli con down "$PROFIL" >/dev/null 2>&1
        sleep 2
        nmcli dev wifi rescan ifname "$IFACE" >/dev/null 2>&1
        sleep 5
        [ -z "$(connexion_active)" ] && nmcli con up "$PROFIL" >/dev/null 2>&1
    else
        nmcli dev wifi rescan ifname "$IFACE" >/dev/null 2>&1
        sleep 4
    fi
    return 0
}

connecter() {
    local ssid=$1 mdp="" nouveau=1 ok=0
    if [ -r "$MDP_TEMP" ]; then mdp=$(cat "$MDP_TEMP"); rm -f "$MDP_TEMP"; fi
    resultat en_cours "$ssid" "Connexion à « $ssid »…"
    nmcli con down "$PROFIL" >/dev/null 2>&1
    nmcli dev wifi rescan ifname "$IFACE" >/dev/null 2>&1
    sleep 3
    connus=$(profils_de "$ssid")
    if [ -n "$connus" ] && [ -z "$mdp" ]; then
        nouveau=0
        nmcli --wait 30 con up id "$(echo "$connus" | head -1)" >/dev/null 2>&1 && ok=1
    else
        # Nouveau mot de passe : on repart d'un profil propre
        echo "$connus" | while IFS= read -r p; do [ -n "$p" ] && nmcli con delete id "$p" >/dev/null 2>&1; done
        if [ -n "$mdp" ]; then
            nmcli --wait 30 dev wifi connect "$ssid" password "$mdp" ifname "$IFACE" >/dev/null 2>&1 && ok=1
        else
            nmcli --wait 30 dev wifi connect "$ssid" ifname "$IFACE" >/dev/null 2>&1 && ok=1
        fi
    fi
    if [ "$ok" = 1 ] && [ "$(connexion_active)" != "" ] && [ "$(connexion_active)" != "$PROFIL" ]; then
        ip4=$(nmcli -g IP4.ADDRESS dev show "$IFACE" | head -1 | cut -d/ -f1)
        log "connecté à « $ssid » ($ip4)"
        resultat ok "$ssid" "Connecté à « $ssid » ($ip4). Page : http://tetenam.local:8080"
        return 0
    fi
    # Échec : on n'enregistre pas un mauvais mot de passe
    if [ "$nouveau" = 1 ]; then
        profils_de "$ssid" | while IFS= read -r p; do [ -n "$p" ] && nmcli con delete id "$p" >/dev/null 2>&1; done
    fi
    log "connexion à « $ssid » impossible"
    if [ -z "$(cable_actif)" ]; then
        # Pas de câble : on revient sur un Wi-Fi connu, sinon sur le hotspot
        nmcli dev connect "$IFACE" >/dev/null 2>&1
        sleep 8
        [ -z "$(connexion_active)" ] && hotspot
    fi
    resultat echec "$ssid" "Connexion à « $ssid » impossible : mot de passe refusé ou réseau trop loin."
    return 1
}

oublier() {
    profils_de "$1" | while IFS= read -r p; do
        [ -n "$p" ] && nmcli con delete id "$p" >/dev/null 2>&1 && log "réseau « $1 » oublié"
    done
    return 0
}

etat() {
    c=$(connexion_active)
    if [ -z "$c" ]; then
        echo "Wi-Fi : aucune connexion"
    elif [ "$c" = "$PROFIL" ]; then
        echo "Wi-Fi : hotspot « $SSID » actif -> http://tetenam.local:8080 ou http://$IP:8080"
    else
        ip4=$(nmcli -g IP4.ADDRESS dev show "$IFACE" | head -1 | cut -d/ -f1)
        echo "Wi-Fi : connecté à « $c » ($ip4) -> http://tetenam.local:8080"
    fi
    echo "Mode au démarrage : $MODE (/etc/tete-nam-wifi.conf)"
}

if ! command -v nmcli >/dev/null; then
    log "ERREUR : NetworkManager absent (nmcli introuvable)"; exit 1
fi
if ! nmcli -t -f DEVICE dev | grep -qx "$IFACE"; then
    log "pas d'interface $IFACE sur cette machine, rien à faire"; exit 0
fi

ACTION=${1:-etat}
case "$ACTION" in
    etat) etat ;;
    auto) besoin_root "$@"; auto ;;
    hotspot|maison)
        besoin_root "$@"
        # Lancé depuis un téléphone ou un SSH branché sur ce même Wi-Fi, la
        # connexion va couper : on fait la bascule en tâche de fond pour
        # qu'elle aille au bout quand même.
        if [ -z "${INVOCATION_ID:-}" ]; then
            log "bascule lancée (la connexion SSH peut couper, c'est normal)"
            exec systemd-run --quiet --collect --unit="tete-nam-wifi-$ACTION" "$0" "$ACTION"
        fi
        "$ACTION" ;;
    chercher) besoin_root "$@"; chercher ;;
    oublier)
        besoin_root "$@"
        [ -n "${2:-}" ] || { log "nom du réseau manquant"; exit 1; }
        oublier "$2" ;;
    connecter)
        besoin_root "$@"
        [ -n "${2:-}" ] || { log "nom du réseau manquant"; exit 1; }
        if [ -z "${INVOCATION_ID:-}" ]; then
            # Mot de passe lu sur l'entrée standard, gardé hors de la ligne de
            # commande jusqu'au dernier moment ; la bascule continue en tâche de
            # fond même si la page qui l'a demandée perd la connexion.
            umask 077
            IFS= read -r MDP || true
            printf '%s' "$MDP" > "$MDP_TEMP"
            resultat en_cours "$2" "Connexion à « $2 »…"
            exec systemd-run --quiet --collect --unit="tete-nam-wifi-connecter-$(date +%s)" "$0" connecter "$2"
        fi
        connecter "$2" ;;
    *) sed -n '5,15p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
