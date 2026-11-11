#!/usr/bin/env python3
"""Serveur web de la Tête NAM : http://tetenam.local:8080

Sert l'interface tactile (ui.html, la même sur l'écran de la tête, dans un
navigateur et dans l'appli Android via Bluetooth) et son API JSON. C'est le
seul programme qui parle au moteur audio stompbox (port 24639).

API (tout en JSON) :
  GET  /api/etat      petit état, demandé chaque seconde (DSP, xruns, preset,
                      compteurs "rev" et "cfg" : quand ils changent, la page
                      redemande /api/tout ou /api/config)
  GET  /api/tout      chaîne de blocs, réglages, presets, réglages audio
  GET  /api/config    types de blocs disponibles, leurs réglages, modèles NAM
  GET  /api/systeme   graphe DSP, température, version
  GET  /api/niveau    niveau du son en sortie, demandé ~30 fois par seconde par
                      l'écran en veille (le moteur n'est interrogé que pendant
                      qu'une page le demande)
  POST /api/param     {id, nom, valeur}
  POST /api/actif     {id, actif}
  POST /api/ordre     {ordre: [id, ...]}
  POST /api/ajouter   {type, apres: id ou null} -> {id}
  POST /api/retirer   {id}
  POST /api/preset    {action: charger|sauver|supprimer|nouveau|suivant|precedent, nom}
  POST /api/calibration {valeur}   POST /api/volume {valeur}
  POST /api/buffer    {taille}     POST /api/accordeur {actif}
  POST /api/veille    {secondes, style}   délai de veille de l'écran (0 = jamais),
                      dessin du son : onde ou spectre
  GET  /api/wifi      connexion en cours, réseaux visibles, dernier résultat
  POST /api/wifi      {action: chercher|connecter|oublier|hotspot, ssid, mdp}
  POST /api/envoyer   fichier .nam brut, nom dans l'en-tête X-Nom-Fichier
  POST /api/midi      {action: apprendre|annuler|oublier|mode|canal, ...}
  GET  /api/tone3000  connexion TONE3000, téléchargement et essai en cours
  POST /api/tone3000  {action: connecter|annuler|deconnecter|liste|tone|telecharger|
                      essayer|garder|abandonner|catalogue|cle|recherche, ...}
  GET  /api/bibliotheque  modèles de la tête avec étiquettes, favoris, fiche TONE3000
  POST /api/bibliotheque  {action: etiqueter|favori|renommer|supprimer|utilisation|
                      nouvelle_etiquette|retirer_etiquette, ...}
  GET  /api/cartes    cartes son branchées et carte utilisée
  POST /api/carte     {id}   change de carte son (relance l'audio)
  GET  /api/mesure    mesure de calibration en cours : niveau lu sur l'entrée
  POST /api/mesure    {action: ports|demarrer|regler|arreter, sortie, entree, freq,
                      niveau, son}   sinus de test pour mesurer les dBu de la carte ;
                      {action: demarrer, mode: carre, entree} : sans multimètre,
                      carré 0 / 3,3 V sur la broche GPIO 23 du Pi
  GET  /tone3000/catalogue   (téléphone) ouvre le catalogue TONE3000 ; le tone
                      choisi revient sur /tone3000/retour puis s'ouvre sur la tête

MIDI (midi.py) : Program Change n -> n-ième preset de la liste (0 = le 1er) ;
Control Change -> ce qu'on lui a fait « apprendre » (bloc allumé/éteint,
réglage, preset suivant/précédent, accordeur). Les assignations des blocs
sont propres à chaque preset, celles de navigation valent pour tous.

Uniquement la bibliothèque standard de Python : rien à installer.
"""
import copy
import hashlib
import html
import ipaddress
import json
import math
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ICI = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, ICI)
import bibliotheque  # noqa: E402
import midi  # noqa: E402
import tetenam  # noqa: E402
import tone3000  # noqa: E402
from bibliotheque import PREFIXE_ESSAI  # noqa: E402
from moteur import CHAINE, Moteur, MoteurAbsent, analyser_programme  # noqa: E402

PORT = int(os.environ.get("TETE_NAM_PORT", "8080"))
DONNEES = tetenam.DONNEES
PRESETS = os.path.join(DONNEES, "Presets")
FICHIER_ETAT = os.path.join(DONNEES, "etat.json")
FICHIER_MIDI = os.path.join(DONNEES, "midi.json")
DOSSIER_MODELES = tetenam.DOSSIER_MODELES
PAGE = os.path.join(ICI, "ui.html")

# Blocs proposés dans l'interface, dans l'ordre du menu « Ajouter » :
# (type stompbox, nom affiché, nom court, catégorie, icône)
TYPES_UI = [
    ("NoiseGate", "Noise gate", "Gate", "dyn", "▤"),
    ("Compressor", "Compresseur", "Comp.", "dyn", "◐"),
    ("Boost", "Boost", "Boost", "drive", "▲"),
    ("Screamer", "Overdrive", "Overdrive", "drive", "◆"),
    ("Fuzz", "Fuzz", "Fuzz", "drive", "✸"),
    ("NAM", "NAM", "NAM", "nam", "▣"),
    ("Wah", "Wah", "Wah", "filtre", "◠"),
    ("AutoWah", "Auto-wah", "Auto-wah", "filtre", "◡"),
    ("EQ-7", "EQ 7 bandes", "EQ", "filtre", "☰"),
    ("HighLow", "Coupe-bas / haut", "Filtre", "filtre", "⌒"),
    ("Chorus", "Chorus", "Chorus", "mod", "≈"),
    ("Flanger", "Flanger", "Flanger", "mod", "∽"),
    ("Phaser", "Phaser", "Phaser", "mod", "◎"),
    ("Vibrato", "Vibrato", "Vibrato", "mod", "≋"),
    ("Tremolo", "Trémolo", "Trémolo", "mod", "⋯"),
    ("Delay", "Délai", "Délai", "temps", "↻"),
    ("Reverb", "Réverbe", "Réverbe", "temps", "∿"),
    ("Level", "Volume", "Volume", "util", "◖"),
]
CATEGORIES = [("dyn", "Dynamique"), ("drive", "Saturation"), ("nam", "Ampli / pédale NAM"),
              ("filtre", "Filtres et EQ"), ("mod", "Modulation"), ("temps", "Délai et réverbe"),
              ("util", "Utilitaires")]
# Noms des réglages (stompbox est en anglais). "Type.Réglage" pour un cas particulier.
LIBELLES = {
    "Gain": "Gain", "Volume": "Volume", "Drive": "Drive", "Tone": "Tonalité", "Level": "Niveau",
    "Thresh": "Seuil", "Ratio": "Ratio", "Attack": "Attaque", "Release": "Relâche",
    "Blend": "Mélange", "Fuzz": "Fuzz", "Octave": "Octave", "Bias": "Polarisation", "Wah": "Pédale",
    "Speed": "Vitesse", "Depth": "Profondeur", "FrqWidth": "Largeur", "FBack": "Réinjection",
    "Rate": "Vitesse", "Shape": "Forme", "Delay": "Temps", "Mix": "Mix", "Warm": "Chaleur",
    "HiLo": "Filtre", "Size": "Taille", "Decay": "Durée", "Model": "Modèle", "Quality": "Qualité",
    "OutputMode": "Sortie", "Low": "Coupe-bas", "High": "Coupe-haut", "Strength": "Force",
    "Soft": "Douceur", "Hold": "Maintien", "Vol": "Volume",
    "NAM.Gain": "Entrée", "NAM.Volume": "Sortie", "Delay.FBack": "Répétitions",
    "Reverb.Blend": "Mix", "Compressor.Blend": "Mix",
}
VALEURS_LISTE = {"Normalized": "Normalisé", "Calibrated": "Calibré"}
VERSION = open(os.path.join(ICI, "VERSION")).read().strip() if os.path.exists(
    os.path.join(ICI, "VERSION")) else "dev"

moteur = Moteur()
verrou = threading.RLock()        # une seule modification à la fois
etat = {
    "rev": 0, "cfg": "", "types": {}, "modeles": [],
    "prog": {"chaines": {}, "valeurs": {}, "actifs": {}},
    "moteur": False, "dsp": None, "histo": deque(maxlen=120), "xruns": 0,
    "chargement": False, "hauteur": -1.0, "accordeur": False, "throttled": "?",
    "preset": "", "modifie": False, "a_sauver": 0.0, "generation": 0, "vus": set(),
}


def journal(*a):
    print(*a, flush=True)


# --- État mémorisé : preset en cours et chaîne en cours (pour le redémarrage) --

def lire_etat():
    try:
        with open(FICHIER_ETAT) as f:
            d = json.load(f)
        etat["preset"], etat["modifie"] = str(d.get("preset", "")), bool(d.get("modifie"))
    except (OSError, ValueError):
        pass


def ecrire_fichier(chemin, texte):
    """Écrit à côté puis renomme : jamais de fichier à moitié écrit."""
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    tmp = chemin + ".tmp"
    with open(tmp, "w") as f:
        f.write(texte)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, chemin)


def sauver_etat():
    ecrire_fichier(FICHIER_ETAT, json.dumps({"preset": etat["preset"], "modifie": etat["modifie"]}))


def sauver_en_cours():
    """La chaîne actuelle, sous forme de commandes stompbox, rejouée au démarrage."""
    p = etat["prog"]
    ids = p["chaines"].get(CHAINE, [])
    lignes = ["SetChain %s %s" % (CHAINE, " ".join(ids))]
    for i in ids:
        lignes.append(f"SetParam {i} Enabled {1 if p['actifs'].get(i, True) else 0}")
        t = type_de(i)
        for nom, val in p["valeurs"].get(i, {}).items():
            prm = param_de(t, nom)
            if prm is None or prm["sortie"]:
                continue
            lignes.append(f'SetParam {i} {nom} "{val}"' if prm["type"] in ("liste", "fichier")
                          else f"SetParam {i} {nom} {val}")
    ecrire_fichier(tetenam.EN_COURS, "\n".join(lignes) + "\n")


def modifie():
    etat["modifie"] = True
    etat["a_sauver"] = time.time()
    etat["rev"] += 1


# --- Lecture du moteur --------------------------------------------------------

def type_de(bloc):
    return bloc.split("_")[0]


def param_de(type_, nom):
    for prm in etat["types"].get(type_, {}).get("params", []):
        if prm["nom"] == nom:
            return prm
    return None


def charger_config():
    types = moteur.config()
    sig = hashlib.md5(json.dumps(types, sort_keys=True).encode()).hexdigest()[:10]
    etat["types"] = types
    nam = param_de("NAM", "Model")
    etat["modeles"] = nam["valeurs"] if nam else []
    etat["cfg"] = sig


def rafraichir():
    p = moteur.programme()
    etat["prog"] = p
    etat["vus"].update(p["chaines"].get(CHAINE, []))
    etat["rev"] += 1


def rejouer(chemin):
    """Renvoie au moteur des commandes enregistrées (chaîne en cours)."""
    with open(chemin) as f:
        for ligne in f:
            ligne = ligne.strip()
            if not ligne or not ligne.startswith(("SetChain " + CHAINE, "SetParam")):
                continue
            ok, _, err = moteur.commande(ligne)
            if not ok:
                journal(f"Ignoré : {ligne} ({err})")


def resynchroniser():
    """Le moteur vient de (re)démarrer, ou la page vient de démarrer."""
    with verrou:
        etat["vus"] = set()
        r = tetenam.reglages()
        moteur.ok(f"SetInputLevelDBu {r['INPUT_DBU']}")
        moteur.ok(f"SetParam Master Volume {r['VOLUME_DB']}")
        charger_config()
        p = moteur.programme()
        if not p["chaines"].get(CHAINE):
            if os.path.exists(tetenam.EN_COURS):
                journal("Chaîne en cours rechargée")
                rejouer(tetenam.EN_COURS)
            elif etat["modeles"]:
                # Toute première fois : un NAM avec le premier modèle trouvé
                moteur.ok(f"SetChain {CHAINE} NAM")
                moteur.commande(f'SetParam NAM Model "{etat["modeles"][0]}"')
        rafraichir()
        etat["accordeur"] = False
        journal("Chaîne prête")


# --- Tâche de fond --------------------------------------------------------------

def temperature():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read()) / 1000
    except (OSError, ValueError):
        return None


def boucle():
    n = 0
    while True:
        try:
            st = moteur.statut()
            if moteur.generation != etat["generation"]:
                etat["generation"] = moteur.generation
                resynchroniser()
            etat.update(moteur=True, dsp=st["dsp"], xruns=st["xruns"],
                        chargement=st["chargement"], hauteur=st["hauteur"])
        except (MoteurAbsent, ValueError, OSError) as e:
            if etat["moteur"]:
                journal("Moteur audio injoignable :", e)
            etat.update(moteur=False, dsp=None)
        etat["histo"].append(etat["dsp"])
        if n % 10 == 0:
            etat["throttled"] = tetenam.sh("vcgencmd get_throttled").replace("throttled=", "") or "?"
        if etat["a_sauver"] and time.time() - etat["a_sauver"] > 1.5:
            etat["a_sauver"] = 0.0
            try:
                with verrou:
                    sauver_en_cours()
                    sauver_etat()
            except OSError as e:
                journal("Sauvegarde impossible :", e)
        n += 1
        time.sleep(1)


# --- API : lecture --------------------------------------------------------------

def liste_presets():
    try:
        noms = [n for n in os.listdir(PRESETS) if not n.startswith(".")]
    except OSError:
        return []
    return sorted(noms, key=lambda n: n.lower())


def numero_preset():
    try:
        return liste_presets().index(etat["preset"]) + 1
    except ValueError:
        return None


NOTES = ["Do", "Do#", "Ré", "Ré#", "Mi", "Fa", "Fa#", "Sol", "Sol#", "La", "La#", "Si"]


def accordeur():
    if not etat["accordeur"]:
        return None
    hz = etat["hauteur"]
    if hz is None or hz < 20:
        return {"actif": True, "hz": None}
    demi = 12 * math.log2(hz / 440.0) + 69          # numéro MIDI (La 440 = 69)
    proche = round(demi)
    return {"actif": True, "hz": round(hz, 1), "note": NOTES[proche % 12],
            "octave": proche // 12 - 1, "cents": round((demi - proche) * 100)}


def api_etat():
    return {"rev": etat["rev"], "cfg": etat["cfg"], "moteur": etat["moteur"], "dsp": etat["dsp"],
            "xruns": etat["xruns"], "temp": temperature(), "throttled": etat["throttled"],
            "preset": etat["preset"], "numero": numero_preset(), "modifie": etat["modifie"],
            "chargement": etat["chargement"], "accordeur": accordeur(), "midi": etat_midi(),
            "bib": BIB.rev, "t3k": T3K.rev + t3k_vivant["rev"]}


# Niveau du son pour la veille de l'écran : un fil interroge le moteur environ
# 30 fois par seconde, seulement si une page a demandé /api/niveau récemment.
niveau = {"valeur": None, "demande": 0.0, "fil": None}
verrou_niveau = threading.Lock()


def boucle_niveau():
    while time.time() - niveau["demande"] < 2:
        try:
            niveau["valeur"] = moteur.niveaux()
            time.sleep(1 / 30)
        except (MoteurAbsent, ValueError, OSError):
            niveau["valeur"] = None
            time.sleep(0.5)
    with verrou_niveau:
        niveau["fil"] = None


def api_niveau():
    with verrou_niveau:
        niveau["demande"] = time.time()
        if niveau["fil"] is None:
            niveau["fil"] = threading.Thread(target=boucle_niveau, daemon=True)
            niveau["fil"].start()
    v = niveau["valeur"]
    return {"ok": v is not None, **(v or {})}


def api_tout():
    p = etat["prog"]
    chaine = []
    for i in p["chaines"].get(CHAINE, []):
        t = type_de(i)
        vals = {}
        for nom, v in p["valeurs"].get(i, {}).items():
            prm = param_de(t, nom)
            if prm is None or prm["sortie"]:
                continue
            vals[nom] = v if prm["type"] in ("liste", "fichier") else tetenam.nombre(v, 0.0)
        chaine.append({"id": i, "type": t, "actif": p["actifs"].get(i, True), "valeurs": vals})
    r = tetenam.reglages()
    return {"rev": etat["rev"], "preset": etat["preset"], "numero": numero_preset(),
            "modifie": etat["modifie"], "presets": liste_presets(), "chaine": chaine,
            "reglages": {k: r[k] for k in ("CARD", "RATE", "PERIOD", "NPERIODS", "INPUT_DBU", "VEILLE", "VEILLE_STYLE")},
            "volume": r["VOLUME_DB"], "buffers": tetenam.BUFFERS,
            "midi": {"canal": M["canal"], "assignations": assignations()}}


def api_config():
    types = {}
    for t, nom, court, cat, icone in TYPES_UI:
        cfg = etat["types"].get(t)
        if cfg is None:
            continue
        params = []
        for prm in cfg["params"]:
            if prm["sortie"]:
                continue
            q = dict(prm)
            q["libelle"] = LIBELLES.get(f"{t}.{prm['nom']}", LIBELLES.get(prm["nom"], prm["nom"]))
            if t in ("EQ-7",) and prm["nom"] != "Vol":
                q["libelle"] = prm["nom"] + " Hz"
            if q["type"] == "liste":
                q["libelles"] = [VALEURS_LISTE.get(v, v) for v in prm["valeurs"]]
            params.append(q)
        types[t] = {"nom": nom, "court": court, "categorie": cat, "icone": icone,
                    "description": cfg["description"], "params": params}
    return {"cfg": etat["cfg"], "types": types, "ordre": [x[0] for x in TYPES_UI],
            "categories": CATEGORIES, "modeles": etat["modeles"]}


def api_systeme():
    return {"histo": list(etat["histo"]), "temp": temperature(), "throttled": etat["throttled"],
            "xruns": etat["xruns"], "dsp": etat["dsp"], "version": VERSION,
            "service": tetenam.sh("systemctl is-active tete-nam")}


# --- API : modifications ----------------------------------------------------------

def chaine():
    return list(etat["prog"]["chaines"].get(CHAINE, []))


def verifier_bloc(d):
    i = str(d.get("id", ""))
    if i not in chaine():
        raise ValueError(f"Bloc inconnu : {i}")
    return i


def texte_sur(v):
    v = str(v)
    if '"' in v or "\n" in v or "\r" in v:
        raise ValueError("Caractère interdit")
    return v


def api_param(d):
    with verrou:
        i = verifier_bloc(d)
        nom = str(d.get("nom", ""))
        prm = param_de(type_de(i), nom)
        if prm is None or prm["sortie"]:
            raise ValueError(f"Réglage inconnu : {nom}")
        if prm["type"] in ("liste", "fichier"):
            v = texte_sur(d.get("valeur", ""))
            if v not in prm["valeurs"]:
                if prm["type"] == "fichier":           # un modèle envoyé entre-temps ?
                    moteur.ok("RescanFiles")
                    charger_config()
                    prm = param_de(type_de(i), nom)
                if v not in prm["valeurs"]:
                    raise ValueError(f"Valeur inconnue : {v}")
            moteur.ok(f'SetParam {i} {nom} "{v}"')
            if essai and essai["bloc"] == i and nom == "Model" and v != essai["fichier"]:
                etat["prog"]["valeurs"].setdefault(i, {})[nom] = v
                nettoyer_essai(restaurer=False)      # un autre modèle choisi : essai abandonné
        else:
            v = min(prm["max"], max(prm["min"], float(d.get("valeur"))))
            if prm["type"] in ("entier", "interrupteur"):
                v = int(round(v))
            moteur.ok(f"SetParam {i} {nom} {v}")
            v = f"{v}"
        etat["prog"]["valeurs"].setdefault(i, {})[nom] = v
        modifie()
        return {"ok": True}


def api_actif(d):
    with verrou:
        i = verifier_bloc(d)
        actif = bool(d.get("actif"))
        moteur.ok(f"SetParam {i} Enabled {1 if actif else 0}")
        etat["prog"]["actifs"][i] = actif
        modifie()
        return {"ok": True}


def mettre_chaine(ids):
    moteur.ok("SetChain %s %s" % (CHAINE, " ".join(ids)))


def api_ordre(d):
    with verrou:
        ordre = [str(x) for x in d.get("ordre", [])]
        if sorted(ordre) != sorted(chaine()):
            raise ValueError("L'ordre ne correspond pas à la chaîne actuelle")
        mettre_chaine(ordre)
        etat["prog"]["chaines"][CHAINE] = ordre
        modifie()
        return {"ok": True}


def api_ajouter(d):
    with verrou:
        t = str(d.get("type", ""))
        if t not in [x[0] for x in TYPES_UI] or t not in etat["types"]:
            raise ValueError(f"Type de bloc inconnu : {t}")
        ids = chaine()
        if len(ids) >= 16:
            raise ValueError("16 blocs au maximum")
        # Un identifiant jamais utilisé depuis le démarrage du moteur : le bloc
        # part de ses réglages par défaut
        n, nouveau = 1, t
        while nouveau in ids or nouveau in etat["vus"]:
            n += 1
            nouveau = f"{t}_{n}"
        apres = d.get("apres")
        pos = ids.index(apres) + 1 if apres in ids else len(ids)
        ids.insert(pos, nouveau)
        mettre_chaine(ids)
        etat["vus"].add(nouveau)
        voulu = str(d.get("modele") or "")
        if t == "NAM" and voulu and voulu not in etat["modeles"]:
            moteur.ok("RescanFiles")
            charger_config()
        if t == "NAM" and voulu in etat["modeles"]:
            moteur.commande(f'SetParam {nouveau} Model "{texte_sur(voulu)}"')
        elif t == "NAM" and etat["modeles"]:
            # Un NAM sans modèle laisse passer le son : on prend le modèle du
            # dernier NAM de la chaîne, ou le premier de la liste
            modele = next((m for m in etat["modeles"] if not m.startswith(PREFIXE_ESSAI)), etat["modeles"][0])
            for autre in reversed(ids):
                v = etat["prog"]["valeurs"].get(autre, {}).get("Model")
                if type_de(autre) == "NAM" and v in etat["modeles"] and not v.startswith(PREFIXE_ESSAI):
                    modele = v
                    break
            moteur.commande(f'SetParam {nouveau} Model "{modele}"')
        rafraichir()
        modifie()
        return {"ok": True, "id": nouveau}


def api_retirer(d):
    with verrou:
        i = verifier_bloc(d)
        retirer_bloc(i)
        if essai and essai["bloc"] == i:
            nettoyer_essai(restaurer=False)
        modifie()
        return {"ok": True}


def nom_preset(v):
    v = re.sub(r"\s+", " ", str(v or "")).strip()
    v = re.sub(r"[^\w \-+().,'&#]", "", v)[:40].strip()
    if not v or v.startswith("."):
        raise ValueError("Donnez un nom au preset")
    return v


def attendre_chargement():
    time.sleep(0.1)
    fin = time.time() + 20
    while time.time() < fin:
        if not moteur.statut()["chargement"]:
            return
        time.sleep(0.1)


def charger_preset(nom):
    if nom not in liste_presets():
        raise ValueError(f"Preset introuvable : {nom}")
    moteur.ok(f'LoadPreset "{nom}"')
    attendre_chargement()
    rafraichir()
    if essai:
        nettoyer_essai(restaurer=False)
    etat["preset"], etat["modifie"] = nom, False
    M["courant"] = copy.deepcopy(M["presets"].get(nom, {}))
    sauver_en_cours()
    sauver_etat()
    sauver_midi()


def api_preset(d):
    with verrou:
        action = d.get("action")
        presets = liste_presets()
        if action == "charger":
            charger_preset(str(d.get("nom", "")))
        elif action in ("suivant", "precedent"):
            if not presets:
                raise ValueError("Aucun preset enregistré")
            if etat["preset"] in presets:
                k = presets.index(etat["preset"]) + (1 if action == "suivant" else -1)
            else:
                k = 0 if action == "suivant" else -1
            charger_preset(presets[k % len(presets)])
        elif action == "sauver":
            nom = nom_preset(d.get("nom") or etat["preset"])
            if essai:
                garder_essai()          # un preset ne doit pas pointer vers un essai
            moteur.ok(f'SavePreset "{nom}"')
            etat["preset"], etat["modifie"] = nom, False
            M["presets"][nom] = copy.deepcopy(M["courant"])
            sauver_midi()
            sauver_etat()
            etat["rev"] += 1
        elif action == "supprimer":
            nom = str(d.get("nom", ""))
            if nom not in presets:
                raise ValueError(f"Preset introuvable : {nom}")
            moteur.ok(f'DeletePreset "{nom}"')
            M["presets"].pop(nom, None)
            sauver_midi()
            if etat["preset"] == nom:
                etat["preset"], etat["modifie"] = "", True
                sauver_etat()
            etat["rev"] += 1
        elif action == "nouveau":
            mettre_chaine([])
            etat["prog"]["chaines"][CHAINE] = []
            if essai:
                nettoyer_essai(restaurer=False)
            etat["preset"] = ""
            M["courant"] = {}
            sauver_midi()
            modifie()
        else:
            raise ValueError("Action inconnue")
        return {"ok": True, "preset": etat["preset"]}


def changer_conf(cle, valeur):
    r = tetenam.reglages()
    r[cle] = valeur
    tetenam.ecrire_conf(r)


def api_calibration(d):
    with verrou:
        v = float(d.get("valeur"))
        if not -60 <= v <= 60:
            raise ValueError("Calibration entre -60 et +60 dBu")
        moteur.ok(f"SetInputLevelDBu {v}")
        changer_conf("INPUT_DBU", v)
        etat["rev"] += 1
        return {"ok": True}


def api_volume(d):
    with verrou:
        v = min(40.0, max(-40.0, float(d.get("valeur"))))
        moteur.ok(f"SetParam Master Volume {v}")
        changer_conf("VOLUME_DB", v)
        etat["rev"] += 1
        return {"ok": True}


def api_veille(d):
    with verrou:
        if "style" in d:
            if d["style"] not in ("onde", "spectre"):
                raise ValueError("Style inconnu")
            changer_conf("VEILLE_STYLE", d["style"])
        if "secondes" in d:
            changer_conf("VEILLE", int(min(600, max(0, float(d.get("secondes"))))))
        etat["rev"] += 1
        return {"ok": True}


def api_accordeur(d):
    with verrou:
        actif = bool(d.get("actif"))
        moteur.ok(f"SetParam Tuner Enabled {1 if actif else 0}")
        etat["accordeur"] = actif
        return {"ok": True}


redemarrage = {"t": 0.0}


def api_buffer(d):
    with verrou:
        taille = int(d.get("taille"))
        if taille not in tetenam.BUFFERS:
            raise ValueError("Taille de buffer non proposée")
        if time.time() - redemarrage["t"] < 10:
            raise ValueError("Redémarrage déjà en cours, patientez")
        r = tetenam.reglages()
        r["PERIOD"], r["NPERIODS"] = taille, tetenam.NPERIODS
        tetenam.ecrire_conf(r)
        # JACK ne change pas de buffer à chaud : on relance le service audio.
        relancer_audio("Buffer enregistré")
        return {"ok": True}


def relancer_audio(quoi):
    """Relance le service audio (JACK + moteur) après un changement de la conf.
    Autorisé sans mot de passe par /etc/sudoers.d/tete-nam (install.sh)."""
    sauver_en_cours()
    arreter_mesure()
    p = subprocess.run(["sudo", "-n", "systemctl", "restart", "--no-block", "tete-nam"],
                       capture_output=True, text=True, timeout=10)
    if p.returncode:
        raise ValueError(f"{quoi}, mais redémarrage refusé : sudo systemctl restart tete-nam")
    redemarrage["t"] = time.time()
    etat["rev"] += 1


TAILLE_MAX = 50 * 1024 * 1024   # 50 Mo, largement plus qu'un .nam


def api_envoyer(nom, contenu):
    """Reçoit un fichier .nam et le range dans ~/nam."""
    nom = os.path.basename(nom).strip().replace(" ", "_")
    nom = re.sub(r"[^\w.\-]", "_", nom)
    if not nom.lower().endswith(".nam") or nom.startswith("."):
        return {"ok": False, "erreur": f"{nom} : seuls les fichiers .nam sont acceptés"}
    try:
        donnees = json.loads(contenu)
        assert isinstance(donnees, dict) and "architecture" in donnees
    except Exception:
        return {"ok": False, "erreur": f"{nom} : ce n'est pas un modèle NAM valide"}
    os.makedirs(DOSSIER_MODELES, exist_ok=True)
    final = os.path.join(DOSSIER_MODELES, nom)
    temp = final + ".envoi"
    with open(temp, "wb") as f:
        f.write(contenu)
    existait = os.path.exists(final)
    os.replace(temp, final)
    BIB.ajouter_fichier(tetenam.nom_modele(final))
    relire_modeles()
    return {"ok": True, "nom": nom, "remplace": existait}


def relire_modeles():
    """Le moteur relit le dossier des modèles (après un ajout ou une suppression)."""
    try:
        with verrou:
            moteur.ok("RescanFiles")
            charger_config()
    except (MoteurAbsent, ValueError):
        pass


# --- MIDI -------------------------------------------------------------------------
# midi.json : {"canal": 0 (= tous) ou 1-16,
#              "global":  {"cc:80": {"cible": "suivant"}, ...},
#              "presets": {"Nom du preset": {"cc:20": {"cible": "actif", "id": "Screamer",
#                                                      "mode": "bascule"}, ...}},
#              "courant": assignations de la chaîne en cours (pas encore enregistrées)}
# Cibles : actif {id, mode bascule|valeur}, param {id, nom}, suivant, precedent, accordeur.
CIBLES_GLOBALES = ("suivant", "precedent", "accordeur")
LIBELLES_GLOBAUX = {"suivant": "Preset suivant", "precedent": "Preset précédent",
                    "accordeur": "Accordeur"}

M = {"canal": 0, "global": {}, "presets": {}, "courant": {}}
midi_vivant = {"dernier": "", "t": 0.0, "attente": None, "appris": "", "ports": None}
file_midi = deque()
signal_midi = threading.Event()


def lire_midi():
    try:
        with open(FICHIER_MIDI) as f:
            d = json.load(f)
        M["canal"] = int(d.get("canal", 0)) if 0 <= int(d.get("canal", 0)) <= 16 else 0
        for k in ("global", "presets", "courant"):
            if isinstance(d.get(k), dict):
                M[k] = d[k]
    except (OSError, ValueError, TypeError):
        pass


def sauver_midi():
    try:
        ecrire_fichier(FICHIER_MIDI, json.dumps(M, ensure_ascii=False, indent=1))
    except OSError as e:
        journal("Sauvegarde MIDI impossible :", e)


def oublier_bloc(i):
    """Un bloc retiré de la chaîne n'a plus d'assignation."""
    avant = len(M["courant"])
    M["courant"] = {k: v for k, v in M["courant"].items() if v.get("id") != i}
    if len(M["courant"]) != avant:
        sauver_midi()


def meme_cible(a, b):
    return all(a.get(k) == b.get(k) for k in ("cible", "id", "nom"))


def libelle_cible(c):
    if c["cible"] in LIBELLES_GLOBAUX:
        return LIBELLES_GLOBAUX[c["cible"]]
    t = etat["types"].get(type_de(c.get("id", "")), {})
    nom = c.get("id", "?")
    court = {x[0]: x[2] for x in TYPES_UI}.get(type_de(nom))
    if court:                                   # « Overdrive 2 » plutôt que « Screamer_2 »
        nom = court + (" " + nom.split("_")[1] if "_" in nom else "")
    if c["cible"] == "actif":
        return f"{nom} : allumé / éteint"
    t_ = type_de(c.get("id", ""))
    prm = param_de(t_, c.get("nom", ""))
    lib = LIBELLES.get(f"{t_}.{c.get('nom')}", LIBELLES.get(c.get("nom"), c.get("nom")))
    return f"{nom} : {lib}" if t and prm else f"{nom} : {c.get('nom')}"


def assignations():
    """Liste pour l'interface : d'abord celles du preset, puis les globales."""
    r = []
    for portee in ("courant", "global"):
        for cle, c in sorted(M[portee].items(), key=lambda x: int(x[0].split(":")[1])):
            r.append(dict(c, cle=cle, cc=int(cle.split(":")[1]), libelle=libelle_cible(c),
                          portee="preset" if portee == "courant" else "global"))
    return r


def midi_recu(source, statut, d1, d2):
    """Appelé par les fils de lecture de midi.py : on note et on met en file."""
    canal = (statut & 0x0F) + 1
    midi_vivant["dernier"] = f"{source} · {midi.decrire(statut, d1, d2)}"
    midi_vivant["t"] = time.time()
    if M["canal"] and canal != M["canal"]:
        return
    if statut & 0xF0 in (0xB0, 0xC0):
        file_midi.append((statut & 0xF0, d1, d2))
        signal_midi.set()


def apprendre(cc, valeur):
    a = midi_vivant["attente"]
    midi_vivant["attente"] = None
    cible = a["cible"]
    cle = f"cc:{cc}"
    portee = "global" if cible["cible"] in CIBLES_GLOBALES else "courant"
    autre = "courant" if portee == "global" else "global"
    # Une cible = un seul contrôleur, un contrôleur = une seule cible
    M[portee] = {k: v for k, v in M[portee].items() if not meme_cible(v, cible)}
    M[autre].pop(cle, None)
    if cible["cible"] == "actif":
        cible = dict(cible, mode="bascule")
    M[portee][cle] = cible
    midi_vivant["appris"] = f"CC {cc} → {libelle_cible(cible)}"
    sauver_midi()
    if portee == "courant":
        modifie()
    else:
        etat["rev"] += 1


def executer_cc(cc, v):
    cle = f"cc:{cc}"
    c = M["courant"].get(cle) or M["global"].get(cle)
    if c is None:
        return
    p = etat["prog"]
    if c["cible"] == "actif":
        i = c.get("id")
        if i not in chaine():
            return
        if c.get("mode") == "valeur":
            actif = v >= 64
        elif v == 0:          # pied « momentané » qui envoie 0 au relâchement
            return
        else:
            actif = not p["actifs"].get(i, True)
        if actif == p["actifs"].get(i, True) and c.get("mode") == "valeur":
            return
        moteur.ok(f"SetParam {i} Enabled {1 if actif else 0}")
        p["actifs"][i] = actif
        modifie()
    elif c["cible"] == "param":
        i, nom = c.get("id"), c.get("nom", "")
        prm = param_de(type_de(i or ""), nom)
        if i not in chaine() or prm is None or prm["type"] not in ("bouton", "entier"):
            return
        x = prm["min"] + (prm["max"] - prm["min"]) * v / 127
        if prm["type"] == "entier":
            x = int(round(x))
        moteur.ok(f"SetParam {i} {nom} {x}")
        p["valeurs"].setdefault(i, {})[nom] = f"{x}"
        modifie()
    elif v == 0:
        return
    elif c["cible"] in ("suivant", "precedent"):
        presets = liste_presets()
        if presets:
            k = presets.index(etat["preset"]) + (1 if c["cible"] == "suivant" else -1) \
                if etat["preset"] in presets else (0 if c["cible"] == "suivant" else -1)
            charger_preset(presets[k % len(presets)])
    elif c["cible"] == "accordeur":
        actif = not etat["accordeur"]
        moteur.ok(f"SetParam Tuner Enabled {1 if actif else 0}")
        etat["accordeur"] = actif
        etat["rev"] += 1


def boucle_midi():
    """Traite la file : les valeurs continues (pédale) sont regroupées, seule la
    dernière compte ; les appuis et les changements de preset sont tous joués."""
    while True:
        signal_midi.wait()
        signal_midi.clear()
        lot = []
        while file_midi:
            lot.append(file_midi.popleft())
        # Plusieurs Program Change d'affilée : seul le dernier compte
        dernier_pc = max((k for k, m in enumerate(lot) if m[0] == 0xC0), default=-1)
        derniere_valeur = {}
        for k, (t, d1, d2) in enumerate(lot):
            if t == 0xB0:
                derniere_valeur[d1] = k
        for k, (t, d1, d2) in enumerate(lot):
            try:
                with verrou:
                    if t == 0xC0 and k == dernier_pc:
                        presets = liste_presets()
                        if d1 < len(presets):
                            charger_preset(presets[d1])
                    elif t == 0xB0:
                        if midi_vivant["attente"]:
                            if time.time() - midi_vivant["attente"]["t"] < 30:
                                apprendre(d1, d2)
                                continue
                            midi_vivant["attente"] = None
                        c = M["courant"].get(f"cc:{d1}") or M["global"].get(f"cc:{d1}")
                        continu = c is not None and (c["cible"] == "param" or
                                                     (c["cible"] == "actif" and c.get("mode") == "valeur"))
                        if continu and derniere_valeur[d1] != k:
                            continue
                        executer_cc(d1, d2)
            except (MoteurAbsent, ValueError, OSError) as e:
                journal("MIDI : commande impossible :", e)


def api_midi(d):
    with verrou:
        action = d.get("action")
        if action == "apprendre":
            c = d.get("cible") or {}
            cible = {"cible": str(c.get("cible", ""))}
            if cible["cible"] in ("actif", "param"):
                cible["id"] = str(c.get("id", ""))
                if cible["id"] not in chaine():
                    raise ValueError(f"Bloc inconnu : {cible['id']}")
                if cible["cible"] == "param":
                    cible["nom"] = str(c.get("nom", ""))
                    prm = param_de(type_de(cible["id"]), cible["nom"])
                    if prm is None or prm["type"] not in ("bouton", "entier"):
                        raise ValueError("Ce réglage ne se pilote pas en MIDI")
            elif cible["cible"] not in CIBLES_GLOBALES:
                raise ValueError("Cible MIDI inconnue")
            midi_vivant["attente"] = {"cible": cible, "t": time.time()}
            midi_vivant["appris"] = ""
        elif action == "annuler":
            midi_vivant["attente"] = None
        elif action == "oublier":
            cle = str(d.get("cle", ""))
            if M["courant"].pop(cle, None) is not None:
                modifie()
            elif M["global"].pop(cle, None) is not None:
                etat["rev"] += 1
            sauver_midi()
        elif action == "mode":
            c = M["courant"].get(str(d.get("cle", "")))
            if not c or c["cible"] != "actif" or d.get("mode") not in ("bascule", "valeur"):
                raise ValueError("Assignation inconnue")
            c["mode"] = d["mode"]
            sauver_midi()
            modifie()
        elif action == "canal":
            canal = int(d.get("canal", 0))
            if not 0 <= canal <= 16:
                raise ValueError("Canal entre 1 et 16 (0 = tous)")
            M["canal"] = canal
            sauver_midi()
            etat["rev"] += 1
        else:
            raise ValueError("Action inconnue")
        return {"ok": True}


def etat_midi():
    a = midi_vivant["attente"]
    if a and time.time() - a["t"] >= 30:
        midi_vivant["attente"] = a = None
    return {"dernier": midi_vivant["dernier"], "age": round(time.time() - midi_vivant["t"], 1)
            if midi_vivant["t"] else None, "attente": bool(a), "appris": midi_vivant["appris"],
            "ports": midi_vivant["ports"].ports() if midi_vivant["ports"] else []}


# --- Wi-Fi (page Système) : lecture directe par nmcli, changements par
# tete-nam-wifi lancé avec sudo (règle posée par install.sh) -------------------

WIFI_CMD = "/usr/local/bin/tete-nam-wifi"
WIFI_RESULTAT = "/run/tete-nam-wifi.json"
WIFI_IFACE = "wlan0"
WIFI_HOTSPOT = "tete-nam-hotspot"


def nmcli(*args, timeout=10):
    try:
        r = subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def champs_nmcli(ligne):
    """Découpe une ligne « nmcli -t » (les « : » des valeurs sont échappés)."""
    return [c.replace("\\:", ":").replace("\\\\", "\\") for c in re.split(r"(?<!\\):", ligne)]


def wifi_connus():
    connus = set()
    for l in nmcli("-t", "-f", "NAME,TYPE", "con", "show").splitlines():
        c = champs_nmcli(l)
        if len(c) >= 2 and c[1] == "802-11-wireless" and c[0] != WIFI_HOTSPOT:
            ssid = nmcli("-g", "802-11-wireless.ssid", "con", "show", c[0]).strip()
            if ssid:
                connus.add(ssid)
    return connus


def wifi_etat():
    connexion, ip, hotspot, cable = "", "", False, ""
    for l in nmcli("-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev").splitlines():
        c = champs_nmcli(l)
        if len(c) < 4:
            continue
        if c[0] == WIFI_IFACE and c[2] == "connected":
            connexion, hotspot = c[3], c[3] == WIFI_HOTSPOT
        if c[1] == "ethernet" and c[2] == "connected":
            cable = c[3]
    if connexion:
        ip = nmcli("-g", "IP4.ADDRESS", "dev", "show", WIFI_IFACE).split("\n")[0].split("/")[0]
    ssid = ""
    if connexion and not hotspot:
        ssid = nmcli("-g", "802-11-wireless.ssid", "con", "show", connexion).strip() or connexion
    return {"ssid": ssid, "ip": ip, "hotspot": hotspot, "cable": cable}


def api_wifi_lire():
    if not os.path.exists(WIFI_CMD) or not nmcli("-t", "-f", "DEVICE", "dev"):
        return {"ok": True, "absent": True}
    connus = wifi_connus()
    vus = {}
    for l in nmcli("-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "dev", "wifi", "list", "--rescan", "no").splitlines():
        c = champs_nmcli(l)
        if len(c) < 4 or not c[1]:
            continue
        ssid, signal = c[1], int(tetenam.nombre(c[2], 0))
        if ssid in vus and vus[ssid]["signal"] >= signal:
            continue
        vus[ssid] = {"ssid": ssid, "signal": signal, "securise": c[3] not in ("", "--"),
                     "actif": c[0].strip() == "*", "connu": ssid in connus}
    e = wifi_etat()
    reseaux = sorted(vus.values(), key=lambda r: (r["ssid"] != e["ssid"], not r["connu"], -r["signal"]))
    try:
        with open(WIFI_RESULTAT) as f:
            resultat = json.load(f)
    except (OSError, ValueError):
        resultat = None
    return {"ok": True, **e, "reseaux": reseaux, "resultat": resultat,
            "hotspot_nom": hotspot_nom()}


def hotspot_nom():
    try:
        with open("/etc/tete-nam-wifi.conf") as f:
            for l in f:
                m = re.match(r"\s*SSID=\"?([^\"\n]*)", l)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return "TeteNAM"


def wifi_cmd(*args, entree=None, timeout=20):
    r = subprocess.run(["sudo", "-n", WIFI_CMD, *args], input=entree, capture_output=True,
                       text=True, timeout=timeout)
    if r.returncode != 0:
        lignes = (r.stdout + r.stderr).strip().splitlines()
        raise ValueError(lignes[-1] if lignes else "Wi-Fi : échec")


def api_wifi(d):
    a = d.get("action")
    ssid = str(d.get("ssid", ""))
    if a in ("connecter", "oublier") and (not ssid or len(ssid) > 32 or "\n" in ssid):
        raise ValueError("Nom de réseau invalide")
    if a == "chercher":
        wifi_cmd("chercher")
    elif a == "connecter":
        mdp = str(d.get("mdp", ""))
        if mdp and not 8 <= len(mdp) <= 63:
            raise ValueError("Le mot de passe Wi-Fi fait entre 8 et 63 caractères")
        if "\n" in mdp:
            raise ValueError("Caractère interdit")
        wifi_cmd("connecter", ssid, entree=mdp + "\n")
    elif a == "oublier":
        wifi_cmd("oublier", ssid)
    elif a == "hotspot":
        wifi_cmd("hotspot")
    else:
        raise ValueError("Action inconnue")
    return {"ok": True}


# --- TONE3000 -----------------------------------------------------------------------
# Connexion par code (QR code sur l'écran), listes et recherche, fiche d'un
# tone, téléchargement des modèles choisis (en tâche de fond) et essai d'un
# modèle avant de le garder. Le client est dans tone3000.py.

T3K = tone3000.Client(os.path.join(DONNEES, "tone3000"), cle=lambda: tetenam.reglages()["T3K_CLE"],
                      version=VERSION, journal=journal)
BIB = bibliotheque.Bibliotheque(os.path.join(DONNEES, "bibliotheque.json"), DOSSIER_MODELES)
FICHIER_ESSAI = os.path.join(DONNEES, "tone3000", "essai.json")
t3k_vivant = {"rev": 0, "tache": None, "choix": None, "catalogues": {}}
tones_vus = {}                 # tone_id -> (heure, fiche, modèles) : 10 min
essai = None                   # {"bloc", "ancien", "fichier", "tone_id", "model_id", "nom", "tone"}
verrou_tache = threading.Lock()


def t3k_change():
    t3k_vivant["rev"] += 1


def fiche_tone(tone_id, frais=False):
    tone_id = int(tone_id)
    v = tones_vus.get(tone_id)
    if v and not frais and time.time() - v[0] < 600:
        return v[1], v[2]
    t, m = T3K.tone(tone_id)
    tones_vus[tone_id] = (time.time(), t, m)
    if len(tones_vus) > 50:
        del tones_vus[min(tones_vus, key=lambda k: tones_vus[k][0])]
    return t, m


def verifier_nam(contenu):
    try:
        d = json.loads(contenu)
        assert isinstance(d, dict) and "architecture" in d
    except Exception:
        raise ValueError("Le fichier reçu n'est pas un modèle NAM valide")


def ecrire_modele(nom, contenu):
    os.makedirs(DOSSIER_MODELES, exist_ok=True)
    final = os.path.join(DOSSIER_MODELES, nom + ".nam")
    tmp = final + ".envoi"
    with open(tmp, "wb") as f:
        f.write(contenu)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, final)


def telecharger_un(tone, m, etiquettes):
    """Télécharge un modèle s'il n'est pas déjà là. Renvoie son nom de fichier."""
    deja = BIB.fichier_du_modele(m["id"])
    if deja:
        if etiquettes:
            e = BIB.liste([deja])["modeles"][0]["etiquettes"]
            BIB.etiqueter(deja, e + [x for x in etiquettes if x not in e])
        return deja, False
    contenu = T3K.telecharger(m["url"])
    verifier_nam(contenu)
    nom = BIB.nouveau_nom(tone["titre"], m["nom"])
    ecrire_modele(nom, contenu)
    BIB.ajouter_t3k(nom, tone, m, etiquettes)
    return nom, True


def tache_telecharger(tache, tone, choisis, etiquettes):
    for m in choisis:
        if tache.get("annule"):
            break
        tache["en_cours"] = m["nom"]
        t3k_change()
        try:
            nom, nouveau = telecharger_un(tone, m, etiquettes)
            tache["fichiers"].append(nom)
            if not nouveau:
                tache["deja"] += 1
        except (tone3000.ErreurT3K, ValueError, OSError) as e:
            tache["erreurs"].append(f"{m['nom']} : {e}")
        tache["fait"] += 1
        t3k_change()
    tache.update(en_cours="", fini=True)
    relire_modeles()
    journal(f"TONE3000 : {tache['fait'] - len(tache['erreurs'])} modèle(s) de « {tone['titre']} » sur la tête")
    t3k_change()


def lancer_telechargement(d):
    tone, modeles = fiche_tone(d.get("tone_id"))
    ids = {int(x) for x in d.get("modeles", [])}
    choisis = [m for m in modeles if m["id"] in ids]
    if not choisis:
        raise ValueError("Choisissez au moins un modèle")
    etiquettes = [bibliotheque.nom_etiquette(x) for x in d.get("etiquettes", [])][:10]
    with verrou_tache:
        t = t3k_vivant["tache"]
        if t and not t["fini"]:
            raise ValueError("Un téléchargement est déjà en cours")
        tache = {"tone_id": tone["id"], "titre": tone["titre"], "total": len(choisis), "fait": 0,
                 "deja": 0, "en_cours": "", "erreurs": [], "fichiers": [], "fini": False, "t": time.time()}
        t3k_vivant["tache"] = tache
    threading.Thread(target=tache_telecharger, args=(tache, tone, choisis, etiquettes), daemon=True).start()
    t3k_change()
    return {"ok": True}


# --- Essai d'un modèle avant de le garder -------------------------------------------
# Le modèle est téléchargé sous le nom _essai-<id> et mis dans un bloc NAM à
# la place de l'ancien. « Garder » le renomme et le range dans la
# bibliothèque ; « Abandonner » remet l'ancien modèle et efface le fichier.

def sauver_essai():
    if essai is None:
        try:
            os.remove(FICHIER_ESSAI)
        except OSError:
            pass
    else:
        ecrire_fichier(FICHIER_ESSAI, json.dumps(essai))


def bloc_pour_essai(voulu, nouveau=False, apres=None):
    """Le bloc NAM où mettre l'essai, et s'il a été créé pour lui (il sera
    alors retiré si on annule)."""
    ids = chaine()
    if not nouveau:
        if voulu in ids and type_de(voulu) == "NAM":
            return voulu, False
        for i in ids:
            if type_de(i) == "NAM":
                return i, False
    if apres not in ids:
        apres = ids[-1] if ids else None
    return api_ajouter({"type": "NAM", "apres": apres})["id"], True


def retirer_bloc(i):
    ids = [x for x in chaine() if x != i]
    mettre_chaine(ids)
    etat["prog"]["chaines"][CHAINE] = ids
    oublier_bloc(i)


def essayer(d):
    global essai
    tone, modeles = fiche_tone(d.get("tone_id"))
    m = next((x for x in modeles if x["id"] == int(d.get("model_id", -1))), None)
    if m is None:
        raise ValueError("Modèle introuvable dans ce tone")
    deja = BIB.fichier_du_modele(m["id"])
    contenu = None if deja else T3K.telecharger(m["url"])    # hors du verrou : peut être long
    if contenu is not None:
        verifier_nam(contenu)
    nouveau = bool(d.get("nouveau"))              # depuis « + » : l'essai va dans un nouveau bloc
    with verrou:
        cree = False
        if essai:
            # Un autre essai pendant l'ajout d'un NAM : on garde le bloc créé pour le premier
            if nouveau and essai.get("cree") and essai["bloc"] in chaine():
                bloc, cree = essai["bloc"], True
                nettoyer_essai(restaurer=False)
                if deja:
                    api_param({"id": bloc, "nom": "Model", "valeur": deja})
                    return {"ok": True, "bloc": bloc, "deja": deja}
                nouveau = False
            else:
                nettoyer_essai(restaurer=True)
        if deja:                                  # déjà sur la tête : on le met, c'est tout
            if nouveau:
                bloc = api_ajouter({"type": "NAM", "apres": d.get("apres"), "modele": deja})["id"]
            else:
                bloc, _ = bloc_pour_essai(str(d.get("bloc") or ""))
                api_param({"id": bloc, "nom": "Model", "valeur": deja})
            return {"ok": True, "bloc": bloc, "deja": deja}
        if not cree:
            bloc, cree = bloc_pour_essai(str(d.get("bloc") or ""), nouveau, d.get("apres"))
        ancien = etat["prog"]["valeurs"].get(bloc, {}).get("Model", "")
        fichier = f"{PREFIXE_ESSAI}{m['id']}"
        ecrire_modele(fichier, contenu)
        moteur.ok("RescanFiles")
        charger_config()
        moteur.ok(f'SetParam {bloc} Model "{fichier}"')
        etat["prog"]["valeurs"].setdefault(bloc, {})["Model"] = fichier
        essai = {"bloc": bloc, "ancien": ancien, "cree": cree, "fichier": fichier, "tone_id": tone["id"],
                 "model_id": m["id"], "nom": m["nom"], "tone": tone["titre"],
                 "fiche": tone, "modele": {k: m[k] for k in ("id", "nom", "taille")}}
        sauver_essai()
        modifie()
        t3k_change()
        return {"ok": True, "bloc": bloc}


def nettoyer_essai(restaurer):
    """Fin d'un essai sans le garder. restaurer : remet l'ancien modèle dans le bloc."""
    global essai
    with verrou:
        e = essai
        if not e:
            return
        essai = None
        bloc = e["bloc"]
        dedans = bloc in chaine() and etat["prog"]["valeurs"].get(bloc, {}).get("Model") == e["fichier"]
        if restaurer and dedans and e.get("cree"):
            retirer_bloc(bloc)                    # bloc créé pour l'essai : on l'enlève
            modifie()
        elif restaurer and dedans:
            ancien = e["ancien"] if e["ancien"] in etat["modeles"] and not e["ancien"].startswith(PREFIXE_ESSAI) \
                else next((x for x in etat["modeles"] if not x.startswith(PREFIXE_ESSAI)), "")
            if ancien:
                moteur.ok(f'SetParam {bloc} Model "{ancien}"')
                etat["prog"]["valeurs"][bloc]["Model"] = ancien
                modifie()
        try:
            os.remove(os.path.join(DOSSIER_MODELES, e["fichier"] + ".nam"))
        except OSError:
            pass
        sauver_essai()
        relire_modeles()
        t3k_change()


def garder_essai(etiquettes=()):
    global essai
    with verrou:
        e = essai
        if not e:
            raise ValueError("Aucun essai en cours")
        tone, m = e["fiche"], e["modele"]         # gardés à l'essai : pas besoin d'Internet
        final = BIB.fichier_du_modele(e["model_id"])
        source = os.path.join(DOSSIER_MODELES, e["fichier"] + ".nam")
        if final:
            os.remove(source)
        else:
            final = BIB.nouveau_nom(tone["titre"], m["nom"])
            os.replace(source, os.path.join(DOSSIER_MODELES, final + ".nam"))
            BIB.ajouter_t3k(final, tone, m, etiquettes)
        essai = None
        sauver_essai()
        moteur.ok("RescanFiles")
        charger_config()
        for i in chaine():                         # le bloc d'essai prend le vrai nom
            if etat["prog"]["valeurs"].get(i, {}).get("Model") == e["fichier"]:
                moteur.ok(f'SetParam {i} Model "{final}"')
                etat["prog"]["valeurs"][i]["Model"] = final
        modifie()
        t3k_change()
        return final


def essai_au_demarrage():
    """La tête s'est arrêtée pendant un essai : on remet l'ancien modèle."""
    try:
        with open(FICHIER_ESSAI) as f:
            e = json.load(f)
    except (OSError, ValueError):
        return
    try:
        with open(tetenam.EN_COURS) as f:
            lignes = f.read().splitlines()
        if e.get("cree"):                         # bloc créé pour l'essai : on l'enlève
            b = e.get("bloc", "")
            lignes = [" ".join(x for x in l.split() if x != b) if l.startswith("SetChain ") else l
                      for l in lignes if not l.startswith(f"SetParam {b} ")]
        else:
            lignes = [l.replace(f'"{e["fichier"]}"', f'"{e.get("ancien", "")}"') for l in lignes]
        ecrire_fichier(tetenam.EN_COURS, "\n".join(lignes) + "\n")
    except OSError:
        pass
    for chemin in (os.path.join(DOSSIER_MODELES, e.get("fichier", "x") + ".nam"), FICHIER_ESSAI):
        try:
            os.remove(chemin)
        except OSError:
            pass
    journal("Essai TONE3000 interrompu : ancien modèle remis")


# --- Catalogue complet sur le téléphone (page « Select » de TONE3000) ------------

def ip_locale(hote=""):
    """Adresse de la tête sur le réseau local (le téléphone doit la joindre)."""
    h = hote.rsplit(":", 1)[0].strip("[]") if hote else ""
    try:
        if ipaddress.ip_address(h).is_private:
            return h
    except ValueError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))        # aucun paquet envoyé
            ip = s.getsockname()[0]
        if ipaddress.ip_address(ip).is_private:
            return ip
    except OSError:
        pass
    return wifi_etat().get("ip", "")


def page_catalogue(hote):
    ip = ip_locale(hote)
    if not ip:
        raise ValueError("La tête n'a pas d'adresse sur le réseau local")
    retour = f"http://{ip}:{PORT}/tone3000/retour"
    url, cle, verif = T3K.adresse_catalogue(retour)
    cat = t3k_vivant["catalogues"]
    for k in [k for k, v in cat.items() if time.time() - v["t"] > 1800]:
        del cat[k]
    cat[cle] = {"verif": verif, "retour": retour, "t": time.time()}
    return url


def retour_catalogue(q):
    """TONE3000 renvoie ici (via le téléphone) le tone choisi."""
    g = lambda k: (q.get(k) or [""])[0]
    c = t3k_vivant["catalogues"].pop(g("state"), None)
    if c is None:
        return "Demande inconnue ou trop ancienne : recommencez depuis la tête."
    if g("error"):
        return "TONE3000 : " + (g("error_description") or g("error"))
    if g("code"):
        T3K.connexion_par_code(g("code"), c["verif"], c["retour"])
    if g("canceled") == "true" or not g("tone_id"):
        return "Aucun tone choisi."
    t3k_vivant["choix"] = {"tone_id": int(g("tone_id")), "n": (t3k_vivant["choix"] or {}).get("n", 0) + 1}
    t3k_change()
    return "C'est envoyé : le tone s'ouvre sur l'écran de la tête."


def api_tone3000_lire():
    r = tetenam.reglages()
    tache = t3k_vivant["tache"]
    if tache and tache["fini"] and time.time() - tache["t"] > 600:
        tache = t3k_vivant["tache"] = None
    return {"ok": True, "cle": bool(r["T3K_CLE"]), "recherche": r["T3K_RECHERCHE"] == "oui",
            **T3K.etat(), "tache": tache and {k: v for k, v in tache.items() if k != "t"},
            "essai": essai and {k: v for k, v in essai.items() if k not in ("fiche", "modele")},
            "choix": t3k_vivant["choix"], "rev": T3K.rev + t3k_vivant["rev"]}


def api_tone3000(d):
    a = d.get("action")
    if a == "cle":
        v = str(d.get("valeur", "")).strip()
        if v.startswith("t3k_cs_"):
            raise ValueError("C'est la clé SECRÈTE : il faut la clé publique t3k_pub_…")
        if v and not re.fullmatch(r"t3k_pub_[\w\-]{4,200}", v):
            raise ValueError("La clé commence par t3k_pub_")
        with verrou:
            changer_conf("T3K_CLE", v)
        return {"ok": True}
    if a == "recherche":
        with verrou:
            changer_conf("T3K_RECHERCHE", "oui" if d.get("actif") else "non")
        etat["rev"] += 1
        return {"ok": True}
    if a == "connecter":
        T3K.demarrer_connexion()
    elif a == "annuler":
        T3K.annuler_connexion()
    elif a == "deconnecter":
        T3K.deconnecter()
    elif a == "liste":
        quoi = str(d.get("quoi", ""))
        if quoi == "search" and tetenam.reglages()["T3K_RECHERCHE"] != "oui":
            raise ValueError("Recherche texte coupée (page Système)")
        r = T3K.liste(quoi, str(d.get("texte", "")), str(d.get("gear", "")), d.get("page", 1),
                      str(d.get("tri", "")))
        chez_moi = {}
        for m in BIB.d["modeles"].values():
            t = (m.get("t3k") or {}).get("tone_id")
            if t is not None:
                chez_moi[t] = chez_moi.get(t, 0) + 1
        for t in r["tones"]:
            t["chez_moi"] = chez_moi.get(t["id"], 0)
        return {"ok": True, **r}
    elif a == "tone":
        t, modeles = fiche_tone(d.get("id"), frais=bool(d.get("frais")))
        return {"ok": True, "tone": t, "modeles": [
            {k: m[k] for k in ("id", "nom", "taille")} | {"fichier": BIB.fichier_du_modele(m["id"])}
            for m in modeles]}
    elif a == "telecharger":
        return lancer_telechargement(d)
    elif a == "stop":
        if t3k_vivant["tache"]:
            t3k_vivant["tache"]["annule"] = True
    elif a == "essayer":
        return essayer(d)
    elif a == "garder":
        return {"ok": True, "fichier": garder_essai(d.get("etiquettes", []))}
    elif a == "abandonner":
        nettoyer_essai(restaurer=True)
    elif a == "catalogue":
        # Adresse courte, à mettre dans un QR code : elle renvoie vers TONE3000
        ip = ip_locale(str(d.get("hote", "")))
        if not ip:
            raise ValueError("La tête n'a pas d'adresse sur le réseau local (Wi-Fi ?)")
        return {"ok": True, "url": f"http://{ip}:{PORT}/tone3000/catalogue"}
    elif a == "vu":
        t3k_vivant["choix"] = None
    else:
        raise ValueError("Action inconnue")
    t3k_change()
    return {"ok": True}


# --- Bibliothèque des modèles ----------------------------------------------------

def api_bibliotheque_lire():
    return {"ok": True, **BIB.liste(etat["modeles"]),
            "essai": essai and {k: essai[k] for k in ("bloc", "fichier", "nom", "tone")}}


def presets_avec(fichier):
    r = []
    for nom in liste_presets():
        try:
            with open(os.path.join(PRESETS, nom), errors="replace") as f:
                p = analyser_programme(f.read().splitlines())
        except OSError:
            continue
        if any(type_de(b) == "NAM" and v.get("Model") == fichier for b, v in p["valeurs"].items()):
            r.append(nom)
    return r


def api_bibliotheque(d):
    a = d.get("action")
    if a in ("nouvelle_etiquette", "retirer_etiquette"):
        (BIB.nouvelle_etiquette if a == "nouvelle_etiquette" else BIB.retirer_etiquette)(d.get("nom", ""))
        return {"ok": True}
    f = texte_sur(d.get("fichier", ""))
    if f not in etat["modeles"] or f.startswith(PREFIXE_ESSAI):
        raise ValueError(f"Modèle inconnu : {f}")
    if a == "etiqueter":
        BIB.etiqueter(f, [str(x) for x in d.get("etiquettes", [])][:20])
    elif a == "favori":
        BIB.favori(f, d.get("favori"))
    elif a == "renommer":
        BIB.renommer(f, d.get("nom", ""))
    elif a == "utilisation":
        dans = [i for i in chaine() if etat["prog"]["valeurs"].get(i, {}).get("Model") == f]
        return {"ok": True, "presets": presets_avec(f), "chaine": dans}
    elif a == "supprimer":
        with verrou:
            if any(etat["prog"]["valeurs"].get(i, {}).get("Model") == f for i in chaine()):
                raise ValueError("Ce modèle est dans la chaîne en cours : changez-le d'abord")
            os.remove(os.path.join(DOSSIER_MODELES, f + ".nam"))
            BIB.oublier(f)
            relire_modeles()
    else:
        raise ValueError("Action inconnue")
    return {"ok": True}


# --- Carte son ---------------------------------------------------------------------
# Chaque carte garde ses propres réglages (entrée, sorties, calibration) dans
# ~/tete-nam/cartes.json : changer de carte remet ceux de la nouvelle.
FICHIER_CARTES = os.path.join(DONNEES, "cartes.json")
PROFILS_CONNUS = {"M4": {"INPUT_DBU": 16.0}}
CARTES_CACHEES = ("vc4hdmi", "Headphones")
CARTES_ALSA = os.environ.get("TETE_NAM_ALSA_CARTES", "/proc/asound/cards")


def cartes_vues():
    """/proc/asound/cards : « 2 [M4 ]: USB-Audio - M4 » puis « MOTU M4 at usb-... »."""
    r = []
    try:
        with open(CARTES_ALSA) as f:
            lignes = f.read().splitlines()
    except OSError:
        return r
    for k, l in enumerate(lignes):
        m = re.match(r"\s*\d+\s+\[(\S+)\s*\]:\s*(.*)", l)
        if m and not m.group(1).startswith(CARTES_CACHEES):
            nom = m.group(2).split(" - ", 1)[-1].strip()
            if k + 1 < len(lignes) and not re.match(r"\s*\d+\s+\[", lignes[k + 1]):
                nom = lignes[k + 1].strip().split(" at ", 1)[0] or nom
            r.append({"id": m.group(1), "nom": nom})
    return r


def lire_profils():
    try:
        with open(FICHIER_CARTES) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def api_cartes_lire():
    r = tetenam.reglages()
    vues = cartes_vues()
    if r["CARD"] not in [c["id"] for c in vues] and r["CARD"] != "aucune":
        vues.append({"id": r["CARD"], "nom": r["CARD"], "absente": True})
    vues.append({"id": "aucune", "nom": "Aucune (essai sans son)"})
    return {"ok": True, "courante": r["CARD"], "cartes": vues}


def api_carte(d):
    with verrou:
        cible = str(d.get("id", ""))
        if cible != "aucune" and cible not in [c["id"] for c in cartes_vues()]:
            raise ValueError(f"Carte {cible} non branchée")
        if time.time() - redemarrage["t"] < 10:
            raise ValueError("Redémarrage déjà en cours, patientez")
        r = tetenam.reglages()
        if cible == r["CARD"]:
            return {"ok": True}
        profils = lire_profils()
        profils[r["CARD"]] = {k: r[k] for k in ("INPUT", "OUTPUTS", "INPUT_DBU")}
        nouveau = PROFILS_CONNUS.get(cible, {}) | profils.get(cible, {})
        r["CARD"] = cible
        for k in ("INPUT", "OUTPUTS", "INPUT_DBU"):
            if k in nouveau:
                r[k] = nouveau[k]
        ecrire_fichier(FICHIER_CARTES, json.dumps(profils, indent=1))
        tetenam.ecrire_conf(r)
        relancer_audio("Carte enregistrée")
        journal(f"Carte son : {cible}")
        return {"ok": True, "calibration": r["INPUT_DBU"], "connue": cible in profils or cible in PROFILS_CONNUS}


# --- Mesure de la calibration de la carte son --------------------------------------
# Outil tete-nam-mesure (mesure-niveau.c) : joue un sinus sur une sortie de la
# carte et mesure le niveau reçu sur une entrée. Avec un multimètre sur le
# câble, on en déduit les dBu qui donnent 0 dBFS (procédure : README, section
# « Mesurer la calibration »). Pendant la mesure, l'outil coupe le moteur des
# sorties de la carte, puis remet tout. Sans nouvelles de la page pendant 30 s
# (GET /api/mesure), il s'arrête seul : un sinus ne reste jamais bloqué.
MESURE_BIN = os.environ.get("TETE_NAM_MESURE", "/opt/tete-nam/tete-nam-mesure")
GPIO_MESURE = 23    # broche 16 du connecteur : carré 0 / 3,3 V de la mesure sans multimètre
mesure = {"proc": None, "lecture": None, "reglage": {}, "erreur": "", "coupees": 0}
verrou_mesure = threading.Lock()


def binaire_mesure():
    for c in (MESURE_BIN, os.path.join(ICI, "tete-nam-mesure")):
        if os.access(c, os.X_OK):
            return c
    raise ValueError("Outil de mesure absent : relancez install.sh")


def ports_carte():
    p = subprocess.run([binaire_mesure(), "--ports"], capture_output=True, text=True, timeout=10)
    try:
        d = json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise ValueError("Outil de mesure : réponse illisible")
    if not d.get("ok"):
        raise ValueError(d.get("erreur") or "Ports de la carte illisibles")
    return d


def lire_sortie_mesure(proc):
    for ligne in proc.stdout:
        try:
            d = json.loads(ligne)
        except ValueError:
            continue
        if d.get("ok") is False:
            mesure["erreur"] = d.get("erreur", "Erreur de mesure")
        elif "lu" in d and mesure["proc"] is proc:
            mesure["lecture"] = d
    proc.wait()
    with verrou_mesure:
        if mesure["proc"] is proc:
            mesure["proc"] = None
            mesure["lecture"] = None


def ecrire_mesure(texte, p=None):
    p = p or mesure["proc"]
    if p is None:
        return False
    try:
        p.stdin.write(texte + "\n")
        p.stdin.flush()
        return True
    except (OSError, ValueError):
        return False


def arreter_mesure():
    with verrou_mesure:
        p = mesure["proc"]
        mesure["proc"] = None
        mesure["lecture"] = None
    if p is None:
        return
    ecrire_mesure("q", p)
    try:
        p.wait(timeout=3)
    except subprocess.TimeoutExpired:
        p.terminate()
        try:
            p.wait(timeout=2)
        except subprocess.TimeoutExpired:
            p.kill()


def lancer_mesure(commande, reglage, texte):
    proc = subprocess.Popen(commande, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    try:
        r = json.loads(proc.stdout.readline())
    except ValueError:
        r = {"ok": False, "erreur": "L'outil de mesure ne répond pas"}
    if not r.get("ok"):
        proc.kill()
        proc.wait()
        raise ValueError(r.get("erreur", "Mesure impossible"))
    with verrou_mesure:
        mesure.update(proc=proc, lecture=None, erreur="", coupees=r.get("coupees", 0), reglage=reglage)
    threading.Thread(target=lire_sortie_mesure, args=(proc,), daemon=True).start()
    journal(f"Mesure : {texte}")
    return {"ok": True}


def api_mesure_lire():
    """Appelé ~3 fois par seconde par la page de mesure : sert aussi de « je suis là »."""
    actif = ecrire_mesure("k")
    r = tetenam.reglages()
    return {"ok": True, "actif": actif, "lecture": mesure["lecture"] if actif else None,
            "reglage": mesure["reglage"], "erreur": mesure["erreur"], "coupees": mesure["coupees"],
            "INPUT_DBU": r["INPUT_DBU"], "CARD": r["CARD"]}


def api_mesure(d):
    a = d.get("action")
    if a == "ports":
        p = ports_carte()
        r = tetenam.reglages()
        sorties_moteur = r["OUTPUTS"].split()
        libres = [x for x in p["sorties"] if x not in sorties_moteur]
        return {"ok": True, "captures": p["captures"], "sorties": p["sorties"],
                "sorties_moteur": sorties_moteur, "frequence": p.get("frequence"),
                "entree": r["INPUT"] if r["INPUT"] in p["captures"] else (p["captures"] or [""])[0],
                "sortie": (libres or p["sorties"] or [""])[0],
                "CARD": r["CARD"], "INPUT_DBU": r["INPUT_DBU"]}
    if a == "demarrer":
        if time.time() - redemarrage["t"] < 10:
            raise ValueError("L'audio redémarre, patientez")
        p = ports_carte()
        sortie, entree = str(d.get("sortie", "")), str(d.get("entree", ""))
        if d.get("mode") == "carre":
            # Sans multimètre : carré 0 / 3,3 V sur une broche GPIO du Pi
            broche = int(d.get("gpio", GPIO_MESURE))
            if entree not in p["captures"] or not 2 <= broche <= 27:
                raise ValueError("Choisissez une entrée de la carte son")
            arreter_mesure()
            return lancer_mesure([binaire_mesure(), "--carre-gpio", str(broche), "--entree", entree],
                                 {"mode": "carre", "entree": entree, "gpio": broche},
                                 f"carré 0/3,3 V sur GPIO {broche}, lecture sur {entree}")
        if sortie not in p["sorties"] or entree not in p["captures"]:
            raise ValueError("Choisissez une sortie et une entrée de la carte son")
        freq = min(20000.0, max(20.0, float(d.get("freq", 1000))))
        niveau_ = min(0.0, max(-60.0, float(d.get("niveau", -10))))
        arreter_mesure()
        return lancer_mesure([binaire_mesure(), "--sortie", sortie, "--entree", entree,
                              "--freq", f"{freq:g}", "--niveau", f"{niveau_:g}"],
                             {"sortie": sortie, "entree": entree, "freq": freq, "niveau": niveau_, "son": True},
                             f"sinus {freq:g} Hz {niveau_:g} dBFS sur {sortie}, lecture sur {entree}")
    if a == "regler":
        if mesure["proc"] is None:
            raise ValueError("Mesure arrêtée")
        g = mesure["reglage"]
        if "freq" in d:
            g["freq"] = min(20000.0, max(20.0, float(d["freq"])))
            ecrire_mesure(f"f {g['freq']:g}")
        if "niveau" in d:
            g["niveau"] = min(0.0, max(-60.0, float(d["niveau"])))
            ecrire_mesure(f"n {g['niveau']:g}")
        if "son" in d:
            g["son"] = bool(d["son"])
            ecrire_mesure(f"s {1 if g['son'] else 0}")
        mesure["lecture"] = None
        return {"ok": True}
    if a == "arreter":
        arreter_mesure()
        return {"ok": True}
    raise ValueError("Action inconnue")


LECTURES = {"/api/etat": api_etat, "/api/tout": api_tout, "/api/config": api_config,
            "/api/systeme": api_systeme, "/api/niveau": api_niveau,
            "/api/wifi": api_wifi_lire, "/api/tone3000": api_tone3000_lire,
            "/api/bibliotheque": api_bibliotheque_lire, "/api/cartes": api_cartes_lire,
            "/api/mesure": api_mesure_lire}
ROUTES = {"/api/param": api_param, "/api/actif": api_actif, "/api/ordre": api_ordre,
          "/api/ajouter": api_ajouter, "/api/retirer": api_retirer, "/api/preset": api_preset,
          "/api/calibration": api_calibration, "/api/volume": api_volume,
          "/api/buffer": api_buffer, "/api/accordeur": api_accordeur, "/api/midi": api_midi,
          "/api/veille": api_veille, "/api/wifi": api_wifi, "/api/tone3000": api_tone3000,
          "/api/bibliotheque": api_bibliotheque, "/api/carte": api_carte,
          "/api/mesure": api_mesure}


class Gestionnaire(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass                                   # pas de bruit dans le journal

    def envoyer(self, code, corps, type_):
        b = corps.encode() if isinstance(corps, str) else corps
        self.send_response(code)
        self.send_header("Content-Type", type_)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def page_simple(self, texte):
        """Petite page pour le téléphone (retour du catalogue TONE3000)."""
        self.envoyer(200, '<!doctype html><meta charset="utf-8"><meta name="viewport" '
                     'content="width=device-width,initial-scale=1"><title>Tête NAM</title>'
                     '<body style="font-family:system-ui;background:#0b0a09;color:#ece6da;'
                     'padding:32px;text-align:center"><h2 style="color:#d6ac5a">Tête NAM</h2>'
                     f'<p>{html.escape(texte)}</p></body>', "text/html; charset=utf-8")

    def json(self, r):
        self.envoyer(200, json.dumps(r, separators=(",", ":")), "application/json")

    def do_GET(self):
        chemin = urllib.parse.urlparse(self.path).path
        if chemin in ("/", "/ui"):
            with open(PAGE, "rb") as f:
                self.envoyer(200, f.read(), "text/html; charset=utf-8")
        elif chemin in ("/tone3000-logo.svg", "/tone3000-marque.svg"):
            with open(os.path.join(ICI, chemin[1:]), "rb") as f:
                self.envoyer(200, f.read(), "image/svg+xml")
        elif chemin == "/logo.png" and os.path.exists(os.path.join(ICI, "logo.png")):
            with open(os.path.join(ICI, "logo.png"), "rb") as f:
                self.envoyer(200, f.read(), "image/png")
        elif chemin == "/tone3000/catalogue":
            try:
                self.send_response(302)
                self.send_header("Location", page_catalogue(self.headers.get("Host", "")))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
            except Exception as e:
                self.page_simple(str(e))
        elif chemin == "/tone3000/retour":
            try:
                texte = retour_catalogue(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query))
            except Exception as e:
                texte = str(e)
            self.page_simple(texte)
        elif chemin in LECTURES:
            try:
                self.json(LECTURES[chemin]())
            except Exception as e:
                self.json({"ok": False, "erreur": str(e)})
        else:
            self.envoyer(404, "introuvable", "text/plain")

    def do_POST(self):
        chemin = urllib.parse.urlparse(self.path).path
        n = int(self.headers.get("Content-Length", 0))
        if chemin == "/api/envoyer":
            if n > TAILLE_MAX:
                return self.json({"ok": False, "erreur": "Fichier trop gros (50 Mo maximum)"})
            nom = urllib.parse.unquote(self.headers.get("X-Nom-Fichier", ""))
            try:
                return self.json(api_envoyer(nom, self.rfile.read(n)))
            except Exception as e:
                return self.json({"ok": False, "erreur": str(e)})
        if chemin not in ROUTES:
            return self.envoyer(404, "introuvable", "text/plain")
        try:
            r = ROUTES[chemin](json.loads(self.rfile.read(n) or b"{}"))
        except MoteurAbsent as e:
            r = {"ok": False, "erreur": str(e)}
        except Exception as e:                  # on renvoie l'erreur à la page
            r = {"ok": False, "erreur": str(e)}
        self.json(r)


def main():
    os.makedirs(PRESETS, exist_ok=True)
    essai_au_demarrage()
    lire_etat()
    lire_midi()
    threading.Thread(target=boucle, daemon=True).start()
    threading.Thread(target=boucle_midi, daemon=True).start()
    entrees = midi.EntreesMidi(midi_recu, uart=tetenam.reglages()["MIDI_UART"],
                               journal=journal)
    midi_vivant["ports"] = entrees
    entrees.demarrer()
    journal(f"Page web : http://{os.uname().nodename}.local:{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Gestionnaire).serve_forever()


if __name__ == "__main__":
    main()
