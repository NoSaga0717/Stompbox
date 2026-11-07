#!/usr/bin/env python3
"""Fabrique la démo en ligne de l'interface (dossier demo/sortie/).

La démo, c'est la vraie page ui.html, plus simulateur.js qui répond à la
place de la tête (pas de son, presets gardés dans le navigateur).

  python3 demo/fabriquer.py               # utilise demo/config-demo.json
  python3 demo/fabriquer.py --capturer    # relit les blocs sur une tête qui
                                          # tourne (http://localhost:8080)
"""
import json, os, shutil, sys, urllib.request

ICI = os.path.dirname(os.path.abspath(__file__))
SORTIE = os.path.join(ICI, "sortie")
MODELES_DEMO = ["Ampli clean A2", "Ampli crunch A2", "Ampli lead A2", "Overdrive A2"]


def capturer(url):
    with urllib.request.urlopen(url + "/api/config", timeout=5) as r:
        cfg = json.load(r)
    # On ne publie pas les noms des modèles de la tête
    cfg["modeles"] = MODELES_DEMO
    for t in cfg["types"].values():
        for p in t["params"]:
            if p["type"] == "fichier":
                p["valeurs"] = MODELES_DEMO
                p["max"] = len(MODELES_DEMO) - 1
    with open(os.path.join(ICI, "config-demo.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)


def main():
    if "--capturer" in sys.argv:
        capturer(os.environ.get("TETE_URL", "http://localhost:8080"))
    os.makedirs(SORTIE, exist_ok=True)
    with open(os.path.join(ICI, "config-demo.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    with open(os.path.join(SORTIE, "config-demo.js"), "w", encoding="utf-8") as f:
        f.write("window.TETE_DEMO_CONFIG = " + json.dumps(cfg, ensure_ascii=False) + ";\n")

    with open(os.path.join(ICI, "..", "ui.html"), encoding="utf-8") as f:
        ui = f.read()
    i = ui.lower().find("<head>")
    if i < 0:
        sys.exit("ui.html : <head> introuvable")
    i += len("<head>")
    ajout = '<script src="config-demo.js"></script><script src="simulateur.js"></script>'
    with open(os.path.join(SORTIE, "ui.html"), "w", encoding="utf-8") as f:
        f.write(ui[:i] + ajout + ui[i:])

    for nom in ("simulateur.js", "index.html", "ecran.html"):
        shutil.copy(os.path.join(ICI, nom), os.path.join(SORTIE, nom))
    shutil.copy(os.path.join(ICI, "..", "demarrage", "logo.png"), os.path.join(SORTIE, "logo.png"))
    print("Démo prête dans", SORTIE)


if __name__ == "__main__":
    main()
