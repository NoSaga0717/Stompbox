#!/usr/bin/env python3
"""Bibliothèque des modèles NAM de la tête : étiquettes, favoris, noms affichés
et fiche TONE3000 de chaque modèle.

Les fichiers .nam restent tous à plat dans ~/nam (le moteur stompbox ne lit
pas les sous-dossiers, et un preset désigne un modèle par son nom de
fichier). Le classement est rangé à part, dans ~/tete-nam/bibliotheque.json :
reclasser ou renommer un modèle ne touche jamais au fichier, donc aucun
preset ne casse.

  {"etiquettes": ["clean", "edge", "crunch", "lead", ...],
   "modeles": {"<nom du fichier sans .nam>": {
       "etiquettes": [...], "favori": false, "nom": "nom affiché choisi" ou "",
       "date": "2026-11-08", "source": "tone3000" ou "fichier",
       "t3k": {"tone_id", "model_id", "tone", "modele", "createur", "avatar",
               "gear", "makes", "tags", "image", "taille", "licence", "url"}}}}
"""
import json
import os
import re
import threading
import time
import unicodedata

ETIQUETTES_DEFAUT = ["clean", "edge", "crunch", "lead"]
PREFIXE_ESSAI = "_essai-"          # modèle en essai (pas encore gardé)


def nom_etiquette(v):
    v = re.sub(r"\s+", " ", str(v or "")).strip().lower()
    v = re.sub(r"[^\w \-+'&]", "", v)[:24].strip()
    if not v:
        raise ValueError("Donnez un nom à l'étiquette")
    return v


def morceau_fichier(v, maxi=40):
    """Partie de nom de fichier sûre pour le moteur (pas d'espace ni de guillemet)."""
    v = unicodedata.normalize("NFKD", str(v or "")).encode("ascii", "ignore").decode()
    v = re.sub(r"[^\w.\-]+", "_", v).strip("._-")
    return re.sub(r"_+", "_", v)[:maxi].strip("._-")


def nom_t3k(t):
    """Nom affiché par défaut d'un modèle TONE3000 : « tone · modèle »."""
    tone, modele = t.get("tone", ""), t.get("modele", "")
    if modele and plat(modele) not in plat(tone):
        return f"{tone} · {modele}" if tone else modele
    return tone or modele


def plat(v):
    return unicodedata.normalize("NFKD", str(v or "")).encode("ascii", "ignore").decode().lower().strip()


class Bibliotheque:
    def __init__(self, chemin, dossier_modeles):
        self.chemin = chemin
        self.dossier = dossier_modeles
        self.verrou = threading.RLock()
        self.rev = 0
        self.d = {"etiquettes": list(ETIQUETTES_DEFAUT), "modeles": {}}
        try:
            with open(chemin) as f:
                d = json.load(f)
            if isinstance(d.get("etiquettes"), list):
                self.d["etiquettes"] = [str(e) for e in d["etiquettes"]]
            if isinstance(d.get("modeles"), dict):
                self.d["modeles"] = d["modeles"]
        except (OSError, ValueError):
            pass

    def sauver(self):
        with self.verrou:
            os.makedirs(os.path.dirname(self.chemin), exist_ok=True)
            tmp = self.chemin + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.d, f, ensure_ascii=False, indent=1)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.chemin)
            self.rev += 1

    def _entree(self, fichier):
        e = self.d["modeles"].setdefault(fichier, {})
        e.setdefault("etiquettes", [])
        e.setdefault("favori", False)
        e.setdefault("nom", "")
        e.setdefault("source", "fichier")
        e.setdefault("date", time.strftime("%Y-%m-%d"))
        return e

    def existe(self, fichier):
        return os.path.exists(os.path.join(self.dossier, fichier + ".nam"))

    # --- Lecture ---------------------------------------------------------------

    def liste(self, fichiers):
        """Les modèles vus par le moteur, avec leur fiche (pour l'interface)."""
        with self.verrou:
            r = []
            for f in fichiers:
                if f.startswith(PREFIXE_ESSAI):
                    continue
                e = self.d["modeles"].get(f, {})
                t = e.get("t3k") or {}
                r.append({"fichier": f, "nom": e.get("nom") or nom_t3k(t) or f.replace("_", " "),
                          "renomme": bool(e.get("nom")), "etiquettes": e.get("etiquettes", []),
                          "favori": bool(e.get("favori")), "source": e.get("source", "fichier"),
                          "date": e.get("date", ""), "t3k": t or None})
            return {"etiquettes": self.d["etiquettes"], "modeles": r, "rev": self.rev}

    def fichier_du_modele(self, model_id):
        """Fichier déjà téléchargé pour ce modèle TONE3000, ou None."""
        with self.verrou:
            for f, e in self.d["modeles"].items():
                if (e.get("t3k") or {}).get("model_id") == model_id and self.existe(f):
                    return f
        return None

    def nouveau_nom(self, tone, modele):
        base = morceau_fichier(tone, 36) or "tone"
        m = morceau_fichier(modele, 36)
        if m and m.lower() != base.lower():
            base += "-" + m
        nom, n = base, 2
        while self.existe(nom) or nom in self.d["modeles"]:
            nom, n = f"{base}_{n}", n + 1
        return nom

    # --- Modifications -----------------------------------------------------------

    def ajouter_t3k(self, fichier, tone, modele, etiquettes=()):
        with self.verrou:
            e = self._entree(fichier)
            e["source"] = "tone3000"
            e["date"] = time.strftime("%Y-%m-%d")
            e["t3k"] = {"tone_id": tone["id"], "model_id": modele["id"], "tone": tone.get("titre", ""),
                        "modele": modele.get("nom", ""), "createur": tone.get("createur", ""),
                        "pseudo": tone.get("pseudo", ""), "avatar": tone.get("avatar", ""),
                        "gear": tone.get("gear", ""), "makes": tone.get("makes", []),
                        "tags": tone.get("tags", []), "image": tone.get("image", ""),
                        "taille": modele.get("taille", ""), "licence": tone.get("licence", ""),
                        "url": tone.get("url", "")}
            for x in etiquettes:
                x = nom_etiquette(x)
                if x not in e["etiquettes"]:
                    e["etiquettes"].append(x)
                if x not in self.d["etiquettes"]:
                    self.d["etiquettes"].append(x)
            self.sauver()

    def ajouter_fichier(self, fichier):
        """Un .nam envoyé à la main : fiche vide, rangée dans « Mes fichiers »."""
        with self.verrou:
            e = self._entree(fichier)
            if e.get("source") != "tone3000":
                e["source"] = "fichier"
            self.sauver()

    def etiqueter(self, fichier, etiquettes):
        with self.verrou:
            propres = []
            for x in etiquettes:
                x = nom_etiquette(x)
                if x not in propres:
                    propres.append(x)
                if x not in self.d["etiquettes"]:
                    self.d["etiquettes"].append(x)
            self._entree(fichier)["etiquettes"] = propres
            self.sauver()

    def favori(self, fichier, oui):
        with self.verrou:
            self._entree(fichier)["favori"] = bool(oui)
            self.sauver()

    def renommer(self, fichier, nom):
        nom = re.sub(r"\s+", " ", str(nom or "")).strip()[:60]
        with self.verrou:
            self._entree(fichier)["nom"] = nom
            self.sauver()

    def oublier(self, fichier):
        with self.verrou:
            self.d["modeles"].pop(fichier, None)
            self.sauver()

    def deplacer(self, ancien, nouveau):
        with self.verrou:
            if ancien in self.d["modeles"]:
                self.d["modeles"][nouveau] = self.d["modeles"].pop(ancien)
                self.sauver()

    def nouvelle_etiquette(self, nom):
        nom = nom_etiquette(nom)
        with self.verrou:
            if nom not in self.d["etiquettes"]:
                self.d["etiquettes"].append(nom)
                self.sauver()
        return nom

    def retirer_etiquette(self, nom):
        nom = nom_etiquette(nom)
        with self.verrou:
            self.d["etiquettes"] = [e for e in self.d["etiquettes"] if e != nom]
            for e in self.d["modeles"].values():
                e["etiquettes"] = [x for x in e.get("etiquettes", []) if x != nom]
            self.sauver()
