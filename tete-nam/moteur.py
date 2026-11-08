#!/usr/bin/env python3
"""Dialogue avec le moteur audio stompbox-jack (port TCP 24639).

Le protocole est en texte : une commande par ligne (terminée par \\r\\n),
le moteur répond par d'éventuelles lignes de données puis "Ok" ou
"Error <raison>". stompbox n'accepte qu'un seul client à la fois : sur la
tête, c'est le serveur web (web.py) qui garde la connexion, les autres
outils passent par lui.

Commandes utilisées :
  SetChain Main A B C        ordre des blocs de la chaîne principale
  SetParam <id> <param> <v>  valeur d'un réglage (Enabled 0/1 = bypass)
  SetParam <id> <param>      remet le réglage à sa valeur par défaut
  Dump Program               état complet (chaînes + réglages)
  Dump Config                description de tous les types de blocs
  Status                     DSP, xruns, preset en chargement, accordeur,
                             crêtes et écrêtages entrée/sortie, fréquence
  SavePreset / LoadPreset / DeletePreset <nom>
  SetInputLevelDBu <dBu>     calibration d'entrée des NAM
  RescanFiles                relit les dossiers de modèles
  Levels                     niveau de sortie des 10 dernières ms et 8 bandes
                             d'octave de 80 Hz à 10 kHz (veille de l'écran)
"""
import shlex
import socket
import threading
import time

HOTE, PORT = "127.0.0.1", 24639
CHAINE = "Main"          # la chaîne de la tête (voir chaine-init.txt)


class MoteurAbsent(Exception):
    pass


def mots(ligne):
    """Découpe une ligne en mots, guillemets compris ("Mon modèle")."""
    try:
        return shlex.split(ligne, posix=True)
    except ValueError:
        return ligne.split()


def nombre(texte, defaut=0.0):
    try:
        return float(texte)
    except (TypeError, ValueError):
        return defaut


class Moteur:
    def __init__(self, hote=HOTE, port=PORT):
        self.hote, self.port = hote, port
        self.verrou = threading.Lock()
        self.sock = None
        self.lecteur = None
        self.generation = 0       # +1 à chaque nouvelle connexion (moteur relancé)

    def _connecter(self, attente):
        fin = time.time() + attente
        while True:
            try:
                s = socket.create_connection((self.hote, self.port), timeout=5)
                s.settimeout(15)
                self.sock, self.lecteur = s, s.makefile("rb")
                self.generation += 1
                return
            except OSError:
                if time.time() >= fin:
                    raise MoteurAbsent("Le moteur audio (stompbox) ne répond pas")
                time.sleep(0.3)

    def fermer(self):
        with self.verrou:
            self._fermer()

    def _fermer(self):
        for x in (self.lecteur, self.sock):
            try:
                if x:
                    x.close()
            except OSError:
                pass
        self.sock = self.lecteur = None

    def commande(self, texte, attente=1.0):
        """Envoie une commande. Renvoie (ok, lignes, erreur)."""
        with self.verrou:
            for essai in (1, 2):
                try:
                    if self.sock is None:
                        self._connecter(attente)
                    self.sock.sendall(texte.encode() + b"\r\n")
                    lignes = []
                    while True:
                        brut = self.lecteur.readline()
                        if not brut:
                            raise OSError("connexion fermée")
                        ligne = brut.decode(errors="replace").rstrip("\r\n")
                        if ligne == "Ok":
                            return True, lignes, ""
                        if ligne.startswith("Error"):
                            return False, lignes, ligne[6:].strip() or "erreur"
                        if ligne:
                            lignes.append(ligne)
                except OSError:
                    self._fermer()
                    if essai == 2:
                        raise MoteurAbsent("Connexion au moteur audio perdue")

    def ok(self, texte):
        ok, _, err = self.commande(texte)
        if not ok:
            raise ValueError(f"{texte} : {err}")

    # --- Lectures ---------------------------------------------------------

    def statut(self):
        _, lignes, _ = self.commande("Status")
        r = {"dsp": None, "xruns": 0, "chargement": False, "hauteur": -1.0,
             "crete_in": None, "crete_out": None, "ecretages_in": 0, "ecretages_out": 0, "frequence": None}
        for l in lignes:
            m = l.split()
            if m[:1] == ["Status"]:
                d = dict(zip(m[1::2], m[2::2]))
                r = {"dsp": nombre(d.get("DSP"), 0) * 100, "xruns": int(nombre(d.get("Xruns"))),
                     "chargement": d.get("Loading") == "1", "hauteur": nombre(d.get("Pitch"), -1),
                     # crêtes (linéaire, 1 = pleine échelle) depuis le dernier Status, nombre de
                     # blocs arrivés à pleine échelle (écrêtage du convertisseur), fréquence JACK
                     "crete_in": nombre(d.get("InPeak"), None), "crete_out": nombre(d.get("OutPeak"), None),
                     "ecretages_in": int(nombre(d.get("InClips"), 0)),
                     "ecretages_out": int(nombre(d.get("OutClips"), 0)),
                     "frequence": int(nombre(d.get("Rate"), 0)) or None}
        return r

    def niveaux(self):
        """Niveau du son en sortie (linéaire, 1 = pleine échelle) : rms, grave
        (sous 250 Hz), aigu et crête, sur les 10 dernières millisecondes."""
        _, lignes, _ = self.commande("Levels", attente=0.2)
        for l in lignes:
            m = l.split()
            if m[:1] == ["Levels"]:
                bandes = []
                if "Bands" in m:
                    i = m.index("Bands")
                    bandes, m = [round(nombre(x), 5) for x in m[i + 1:]], m[:i]
                d = dict(zip(m[1::2], m[2::2]))
                r = {k: round(nombre(d.get(c)), 5) for k, c in
                     (("rms", "Rms"), ("grave", "Low"), ("aigu", "High"), ("crete", "Peak"))}
                r["bandes"] = bandes        # 8 octaves, 80 Hz à 10 kHz (grave -> aigu)
                return r
        return None

    def programme(self):
        """État actuel : chaînes, réglages, blocs activés, calibration."""
        _, lignes, _ = self.commande("Dump Program")
        return analyser_programme(lignes)

    def config(self):
        _, lignes, _ = self.commande("Dump Config")
        return analyser_config(lignes)


def analyser_programme(lignes):
    p = {"chaines": {}, "valeurs": {}, "actifs": {}, "preset": "", "entree_dbu": None}
    for l in lignes:
        m = mots(l)
        if not m:
            continue
        if m[0] == "SetChain" and len(m) >= 2:
            p["chaines"][m[1]] = m[2:]
        elif m[0] == "SetPluginSlot" and len(m) >= 2:
            p["chaines"][m[1]] = m[2:3]
        elif m[0] == "SetParam" and len(m) >= 3:
            bloc, nom, val = m[1], m[2], " ".join(m[3:])
            if nom == "Enabled":
                p["actifs"][bloc] = val == "1"
            else:
                p["valeurs"].setdefault(bloc, {})[nom] = val
        elif m[0] == "SetPreset":
            p["preset"] = " ".join(m[1:])
        elif m[0] == "SetInputLevelDBu" and len(m) > 1:
            p["entree_dbu"] = nombre(m[1], None)
    return p


TYPES_PARAM = {"Knob": "bouton", "VSlider": "bouton", "Power": "bouton", "Int": "entier",
               "Bool": "interrupteur", "Enum": "liste", "File": "fichier"}


def analyser_config(lignes):
    """Dump Config -> {type: {"couleur", "selectionnable", "description",
    "params": [{"nom", "type", "min", "max", "defaut", "format", "avance",
    "sortie", "description", "valeurs"}]}}"""
    types, courant = {}, None
    for l in lignes:
        m = mots(l)
        if not m:
            continue
        if m[0] == "PluginConfig" and len(m) >= 2:
            d = dict(zip(m[2::2], m[3::2]))
            courant = types[m[1]] = {
                "couleur": d.get("BackgroundColor", "#eeeeee"),
                "selectionnable": d.get("IsUserSelectable", "1") == "1",
                "description": d.get("Description", ""), "params": []}
        elif m[0] == "ParameterConfig" and courant is not None and len(m) >= 3:
            # "ParameterConfig <id> <nom> Type Knob MinValue ..." (l'id peut être vide)
            i = m.index("Type") if "Type" in m else 3
            nom = m[i - 1]
            d = dict(zip(m[i::2], m[i + 1::2]))
            courant["params"].append({
                "nom": nom, "type": TYPES_PARAM.get(d.get("Type"), "bouton"),
                "min": nombre(d.get("MinValue")), "max": nombre(d.get("MaxValue"), 1),
                "defaut": nombre(d.get("DefaultValue")), "format": d.get("ValueFormat", ""),
                "avance": d.get("IsAdvanced") == "1", "sortie": d.get("IsOutput") == "1",
                "description": d.get("Description", ""), "valeurs": []})
        elif m[0] in ("ParameterEnumValues", "ParameterFileTree") and courant is not None:
            nom = m[2] if len(m) > 2 else ""
            valeurs = m[3:] if m[0] == "ParameterEnumValues" else m[4:]
            for prm in courant["params"]:
                if prm["nom"] == nom:
                    prm["valeurs"] = valeurs
    return types
