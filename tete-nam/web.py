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
  POST /api/param     {id, nom, valeur}
  POST /api/actif     {id, actif}
  POST /api/ordre     {ordre: [id, ...]}
  POST /api/ajouter   {type, apres: id ou null} -> {id}
  POST /api/retirer   {id}
  POST /api/preset    {action: charger|sauver|supprimer|nouveau|suivant|precedent, nom}
  POST /api/calibration {valeur}   POST /api/volume {valeur}
  POST /api/buffer    {taille}     POST /api/accordeur {actif}
  POST /api/envoyer   fichier .nam brut, nom dans l'en-tête X-Nom-Fichier

Uniquement la bibliothèque standard de Python : rien à installer.
"""
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ICI = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, ICI)
import tetenam  # noqa: E402
from moteur import CHAINE, Moteur, MoteurAbsent  # noqa: E402

PORT = int(os.environ.get("TETE_NAM_PORT", "8080"))
DONNEES = tetenam.DONNEES
PRESETS = os.path.join(DONNEES, "Presets")
FICHIER_ETAT = os.path.join(DONNEES, "etat.json")
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
            "chargement": etat["chargement"], "accordeur": accordeur()}


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
            "reglages": {k: r[k] for k in ("CARD", "RATE", "PERIOD", "NPERIODS", "INPUT_DBU")},
            "volume": r["VOLUME_DB"], "buffers": tetenam.BUFFERS}


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
        if t == "NAM" and etat["modeles"]:
            # Un NAM sans modèle laisse passer le son : on prend le modèle du
            # dernier NAM de la chaîne, ou le premier de la liste
            modele = etat["modeles"][0]
            for autre in reversed(ids):
                v = etat["prog"]["valeurs"].get(autre, {}).get("Model")
                if type_de(autre) == "NAM" and v in etat["modeles"]:
                    modele = v
                    break
            moteur.commande(f'SetParam {nouveau} Model "{modele}"')
        rafraichir()
        modifie()
        return {"ok": True, "id": nouveau}


def api_retirer(d):
    with verrou:
        i = verifier_bloc(d)
        ids = [x for x in chaine() if x != i]
        mettre_chaine(ids)
        etat["prog"]["chaines"][CHAINE] = ids
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
    etat["preset"], etat["modifie"] = nom, False
    sauver_en_cours()
    sauver_etat()


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
            moteur.ok(f'SavePreset "{nom}"')
            etat["preset"], etat["modifie"] = nom, False
            sauver_etat()
            etat["rev"] += 1
        elif action == "supprimer":
            nom = str(d.get("nom", ""))
            if nom not in presets:
                raise ValueError(f"Preset introuvable : {nom}")
            moteur.ok(f'DeletePreset "{nom}"')
            if etat["preset"] == nom:
                etat["preset"], etat["modifie"] = "", True
                sauver_etat()
            etat["rev"] += 1
        elif action == "nouveau":
            mettre_chaine([])
            etat["prog"]["chaines"][CHAINE] = []
            etat["preset"] = ""
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
        sauver_en_cours()
        # JACK ne change pas de buffer à chaud : on relance le service audio.
        # Autorisé sans mot de passe par /etc/sudoers.d/tete-nam (install.sh).
        p = subprocess.run(["sudo", "-n", "systemctl", "restart", "--no-block", "tete-nam"],
                           capture_output=True, text=True, timeout=10)
        if p.returncode:
            raise ValueError("Buffer enregistré, mais redémarrage refusé : "
                             "sudo systemctl restart tete-nam")
        redemarrage["t"] = time.time()
        etat["rev"] += 1
        return {"ok": True}


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
    try:
        with verrou:
            moteur.ok("RescanFiles")
            charger_config()
    except (MoteurAbsent, ValueError):
        pass
    return {"ok": True, "nom": nom, "remplace": existait}


LECTURES = {"/api/etat": api_etat, "/api/tout": api_tout, "/api/config": api_config,
            "/api/systeme": api_systeme}
ROUTES = {"/api/param": api_param, "/api/actif": api_actif, "/api/ordre": api_ordre,
          "/api/ajouter": api_ajouter, "/api/retirer": api_retirer, "/api/preset": api_preset,
          "/api/calibration": api_calibration, "/api/volume": api_volume,
          "/api/buffer": api_buffer, "/api/accordeur": api_accordeur}


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

    def json(self, r):
        self.envoyer(200, json.dumps(r, separators=(",", ":")), "application/json")

    def do_GET(self):
        chemin = urllib.parse.urlparse(self.path).path
        if chemin in ("/", "/ui"):
            with open(PAGE, "rb") as f:
                self.envoyer(200, f.read(), "text/html; charset=utf-8")
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
    lire_etat()
    threading.Thread(target=boucle, daemon=True).start()
    journal(f"Page web : http://{os.uname().nodename}.local:{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Gestionnaire).serve_forever()


if __name__ == "__main__":
    main()
