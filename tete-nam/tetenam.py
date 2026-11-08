#!/usr/bin/env python3
"""Outil en ligne de commande de la Tête NAM (moteur stompbox).

  tete-nam etat          résumé : service, DSP, xruns, température, chaîne
  tete-nam cpu           charge DSP
  tete-nam presets       liste des presets
  tete-nam preset NOM    charge un preset
  tete-nam migrer        réécrit /etc/tete-nam.conf au format actuel

Le moteur audio (stompbox) n'accepte qu'un client à la fois et c'est la page
web qui le garde : ces commandes passent donc par la page web
(http://127.0.0.1:8080). Ce fichier contient aussi la lecture et l'écriture
des réglages /etc/tete-nam.conf, utilisées par web.py.
"""
import json
import os
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.request

CONF = os.environ.get("TETE_NAM_CONF", "/etc/tete-nam.conf")
# Dossier de travail du moteur : Presets/, NAM (lien vers ~/nam), état en cours
DONNEES = os.environ.get("TETE_NAM_DONNEES", os.path.expanduser("~/tete-nam"))
DOSSIER_MODELES = os.environ.get("TETE_NAM_MODELES", os.path.expanduser("~/nam"))
EN_COURS = os.path.join(DONNEES, "en-cours.txt")
WEB = "http://127.0.0.1:%s" % os.environ.get("TETE_NAM_PORT", "8080")

BUFFERS = (64, 128, 256, 512)
NPERIODS = 2
INPUT_DBU_DEFAUT = 16.0
VEILLE_DEFAUT = 7          # secondes sans toucher avant la veille de l'écran


def lire_conf():
    conf = {}
    try:
        with open(CONF) as f:
            for ligne in f:
                ligne = ligne.strip()
                if not ligne or ligne.startswith("#") or "=" not in ligne:
                    continue
                cle, val = ligne.split("=", 1)
                try:
                    conf[cle.strip()] = " ".join(shlex.split(val))
                except ValueError:
                    conf[cle.strip()] = val.strip()
    except OSError:
        pass
    return conf


def nombre(texte, defaut, mini=None, maxi=None):
    try:
        v = float(texte)
    except (TypeError, ValueError):
        return defaut
    if mini is not None:
        v = max(mini, v)
    if maxi is not None:
        v = min(maxi, v)
    return v


def reglages(c=None):
    if c is None:
        c = lire_conf()
    periode = int(nombre(c.get("PERIOD"), 128))
    return {
        "CARD": c.get("CARD", "M4"),
        "RATE": int(nombre(c.get("RATE"), 48000)),
        "PERIOD": periode if periode in BUFFERS else 128,
        "NPERIODS": int(nombre(c.get("NPERIODS"), NPERIODS, 2, 3)),
        "INPUT": c.get("INPUT", "system:capture_1"),
        "OUTPUTS": c.get("OUTPUTS", "system:playback_1 system:playback_2"),
        "INPUT_DBU": nombre(c.get("INPUT_DBU"), INPUT_DBU_DEFAUT, -60, 60),
        "VOLUME_DB": nombre(c.get("VOLUME_DB"), 0.0, -40, 40),
        "ECRAN": "non" if c.get("ECRAN", "oui") == "non" else "oui",
        "ECRAN_ROTATION": c.get("ECRAN_ROTATION", "0") if c.get("ECRAN_ROTATION", "0") in ("0", "90", "180", "270") else "0",
        "MIDI_UART": c.get("MIDI_UART", "") if c.get("MIDI_UART", "").startswith("/dev/") else "",
        "VEILLE": int(nombre(c.get("VEILLE"), VEILLE_DEFAUT, 0, 600)),
        "VEILLE_STYLE": "spectre" if c.get("VEILLE_STYLE") == "spectre" else "onde",
        "T3K_CLE": c.get("T3K_CLE", "") if re.fullmatch(r"t3k_pub_[\w\-]{4,200}", c.get("T3K_CLE", "")) else "",
        "T3K_RECHERCHE": "non" if c.get("T3K_RECHERCHE") == "non" else "oui",
    }


MODELE_CONF = """\
# Réglages de la Tête NAM (lu au démarrage par le service tete-nam)
# La page web réécrit ce fichier quand on change un réglage audio.
# À la main : sudo nano /etc/tete-nam.conf  puis  sudo systemctl restart tete-nam
# (La chaîne d'effets et les presets ne sont pas ici : ils sont dans ~/tete-nam)

# Carte son (nom ALSA, visible avec : cat /proc/asound/cards)
# CARD=aucune : mode test sans carte son (l'interface marche, pas de son)
CARD={CARD}

# Fréquence d'échantillonnage : doit être celle des modèles (48 kHz en général)
RATE={RATE}

# Taille du buffer JACK : 64, 128, 256 ou 512 (réglable depuis la page)
PERIOD={PERIOD}

# Nombre de périodes JACK (2 = le moins de latence)
NPERIODS={NPERIODS}

# Entrée guitare (capture_1 = entrée 1 de la carte son)
INPUT={INPUT}

# Sorties vers le power amp (une ou deux, séparées par un espace)
OUTPUTS="{OUTPUTS}"

# Calibration d'entrée : niveau en dBu qui donne 0 dBFS sur l'entrée de la
# carte son (MOTU M4 : 16). Appliquée à tous les blocs NAM.
INPUT_DBU={INPUT_DBU:g}

# Volume général en sortie, en dB (-40 à +40)
VOLUME_DB={VOLUME_DB:g}

# Écran tactile de la tête : oui ou non (non = pas d'interface sur l'écran,
# seulement la page web et l'appli). Après changement :
#   sudo systemctl restart tete-nam-ecran
ECRAN={ECRAN}

# Rotation de l'image de l'écran : 0, 90, 180 ou 270 (écran monté à l'envers : 180)
ECRAN_ROTATION={ECRAN_ROTATION}

# Entrée MIDI DIN sur le port série du Pi (carte maison) : ex. /dev/ttyAMA0
# Vide = pas d'entrée série. Les entrées MIDI USB et la prise MIDI IN de la
# carte son sont toujours lues, sans réglage. Après changement :
#   sudo systemctl restart tete-nam-web
MIDI_UART={MIDI_UART}

# Veille de l'écran de la tête : après ce nombre de secondes sans toucher
# l'écran, il n'affiche plus que le logo et le niveau du son (0 = jamais).
# Réglable depuis la page Système.
VEILLE={VEILLE}
# Dessin du son pendant la veille : onde (vagues dorées) ou spectre (graves à
# gauche, aigus à droite)
VEILLE_STYLE={VEILLE_STYLE}

# TONE3000 (chercher et télécharger des modèles NAM depuis la tête, page
# Système -> TONE3000). Clé de l'application : t3k_pub_... créée sur
# tone3000.com (Settings -> API Keys). Ce n'est pas un mot de passe, mais
# ne mettez JAMAIS ici la clé secrète t3k_cs_...
T3K_CLE={T3K_CLE}
# Recherche texte dans tout le catalogue : oui ou non. Permise par TONE3000
# pendant le développement ; une tête distribuée sans accord commercial
# avec TONE3000 doit la couper (non).
T3K_RECHERCHE={T3K_RECHERCHE}
"""


def ecrire_conf(r):
    texte = MODELE_CONF.format(**r)
    # Réécriture sur place (le dossier /etc n'est pas à nous), puis sync
    with open(CONF, "w") as f:
        f.write(texte)
        f.flush()
        os.fsync(f.fileno())


def nom_modele(chemin):
    """Les modèles sont désignés par leur nom de fichier sans .nam."""
    return os.path.splitext(os.path.basename(chemin))[0]


def migrer():
    """Ancienne version (Pi 3, mod-host) : la pédale et l'ampli NAM deviennent
    les deux premiers blocs de la chaîne, si aucune chaîne n'existe encore."""
    c = lire_conf()
    if "AMPLI_MODEL" in c and not os.path.exists(EN_COURS):
        lignes, ids = [], []
        for cle, bloc in (("PEDALE", "NAM"), ("AMPLI", "NAM_2")):
            modele = c.get(cle + "_MODEL", "")
            if not modele:
                continue
            ids.append(bloc)
            mode = "Calibrated" if c.get(cle + "_MODE", "") == "calibrated" else "Normalized"
            lignes += [f"SetParam {bloc} Enabled {0 if c.get(cle + '_ACTIF') == '0' else 1}",
                       f'SetParam {bloc} Model "{nom_modele(modele)}"',
                       f"SetParam {bloc} OutputMode {mode}",
                       f"SetParam {bloc} Quality {nombre(c.get(cle + '_QUALITY'), 1.0, 0, 1)}",
                       f"SetParam {bloc} Gain {nombre(c.get(cle + '_IN_DB'), 0.0)}",
                       f"SetParam {bloc} Volume {nombre(c.get(cle + '_OUT_DB'), 0.0)}"]
        if ids:
            os.makedirs(DONNEES, exist_ok=True)
            with open(EN_COURS, "w") as f:
                f.write("\n".join(["SetChain Main " + " ".join(ids)] + lignes) + "\n")
            print(f"Ancienne chaîne Pédale -> Ampli reprise dans {EN_COURS}")
    ecrire_conf(reglages(c))
    print(f"{CONF} réécrit au format actuel")


# --- Commandes (via la page web) -----------------------------------------------

def api(chemin, corps=None):
    data = json.dumps(corps).encode() if corps is not None else None
    req = urllib.request.Request(WEB + chemin, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, OSError, ValueError) as e:
        sys.exit(f"La page web de la tête ne répond pas ({e}). "
                 "Service : systemctl status tete-nam-web")


def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except Exception:
        return "?"


def etat():
    print("Service audio :", sh("systemctl is-active tete-nam"))
    print("Page web      :", sh("systemctl is-active tete-nam-web"))
    e = api("/api/etat")
    t = api("/api/tout")
    print("Moteur        :", "OK" if e.get("moteur") else "ne répond pas")
    print("Charge DSP    :", "–" if e.get("dsp") is None else f"{e['dsp']:.1f} %")
    print("Xruns         :", e.get("xruns"), "(depuis le démarrage du moteur)")
    print("Température   :", "–" if e.get("temp") is None else f"{e['temp']:.0f} °C")
    thr = e.get("throttled", "?")
    print("Throttling    :", thr, "(0x0 = parfait)" if thr == "0x0"
          else "(PAS 0x0 : alim trop faible ou trop chaud)")
    r = t.get("reglages", {})
    print(f"Audio         : {r.get('RATE')} Hz, buffer {r.get('PERIOD')} x {r.get('NPERIODS')}, "
          f"entrée {r.get('INPUT_DBU')} dBu")
    print("Preset        :", (t.get("preset") or "(sans nom)") + (" (modifié)" if t.get("modifie") else ""))
    for b in t.get("chaine", []):
        v = b.get("valeurs", {})
        detail = f" : {v.get('Model') or 'aucun modèle'}" if b["type"] == "NAM" else ""
        print(f"  {'●' if b['actif'] else '○'} {b['id']}{detail}")


def main():
    a = sys.argv[1:]
    if a[:1] == ["etat"]:
        etat()
    elif a[:1] == ["cpu"]:
        e = api("/api/etat")
        print("–" if e.get("dsp") is None else f"{e['dsp']:.1f} %")
    elif a[:1] == ["presets"]:
        t = api("/api/tout")
        for i, p in enumerate(t.get("presets", []), 1):
            print(f"{i:3d}  {p}{'  <- actuel' if p == t.get('preset') else ''}")
    elif a[:1] == ["preset"] and len(a) >= 2:
        r = api("/api/preset", {"action": "charger", "nom": " ".join(a[1:])})
        print("Preset chargé" if r.get("ok") else r.get("erreur"))
    elif a[:1] == ["migrer"]:
        migrer()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
