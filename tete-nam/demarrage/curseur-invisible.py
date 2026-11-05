#!/usr/bin/env python3
"""Crée un thème de curseur invisible (1 pixel transparent) pour l'écran de
la tête : sur un écran tactile, la flèche de la souris n'a rien à faire là.
Usage : curseur-invisible.py /usr/share/icons/tete-nam-invisible"""
import os, struct, sys

dossier = sys.argv[1]
curseurs = os.path.join(dossier, "cursors")
os.makedirs(curseurs, exist_ok=True)

# Format Xcursor : en-tête, une entrée de table, une image 1x1 ARGB transparente
TYPE_IMAGE, TAILLE = 0xFFFD0002, 24
image = struct.pack("<IIIIIIIII", 36, TYPE_IMAGE, TAILLE, 1, 1, 1, 0, 0, 0) + struct.pack("<I", 0)
entete = b"Xcur" + struct.pack("<III", 16, 0x10000, 1)
table = struct.pack("<III", TYPE_IMAGE, TAILLE, 16 + 12)
with open(os.path.join(curseurs, "left_ptr"), "wb") as f:
    f.write(entete + table + image)

NOMS = """default arrow top_left_arrow pointer hand hand1 hand2 pointing_hand text xterm
ibeam wait watch progress left_ptr_watch crosshair cross move fleur grab grabbing
not-allowed col-resize row-resize ew-resize ns-resize nesw-resize nwse-resize
e-resize w-resize n-resize s-resize ne-resize nw-resize se-resize sw-resize
sb_h_double_arrow sb_v_double_arrow all-scroll context-menu help question_arrow
copy alias cell vertical-text zoom-in zoom-out dnd-none dnd-move dnd-copy""".split()
for n in NOMS:
    lien = os.path.join(curseurs, n)
    if os.path.lexists(lien):
        os.remove(lien)
    os.symlink("left_ptr", lien)

with open(os.path.join(dossier, "index.theme"), "w") as f:
    f.write("[Icon Theme]\nName=tete-nam-invisible\nComment=Curseur invisible (ecran tactile)\n")
