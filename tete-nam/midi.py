#!/usr/bin/env python3
"""Lecture des entrées MIDI de la Tête NAM (bibliothèque standard seulement).

Sources lues :
  - tous les ports MIDI ALSA « bruts » (/dev/snd/midiC*D*) : prise MIDI IN de
    la MOTU M4, câble USB-MIDI, pédalier USB… Branchés à chaud : un nouveau
    port est pris en compte en 2 secondes, un port débranché est oublié.
  - en option, un port série (UART du Pi, entrée DIN optocouplée de la carte
    finale) à 31 250 bauds : clé MIDI_UART=/dev/ttyAMA0 de /etc/tete-nam.conf.

Chaque message complet (Program Change, Control Change…) est passé à la
fonction de rappel : rappel(source, statut, data1, data2). Les messages
système (horloge, active sensing, SysEx) sont ignorés.

Test sans la page web : python3 midi.py  (affiche les messages reçus)
"""
import array
import fcntl
import glob
import os
import struct
import termios
import threading
import time
import tty

# Nombre d'octets de données selon le type de message (statut & 0xF0)
DONNEES = {0x80: 2, 0x90: 2, 0xA0: 2, 0xB0: 2, 0xC0: 1, 0xD0: 1, 0xE0: 2}


class Analyseur:
    """Découpe un flux d'octets MIDI en messages (gère le « running status »)."""

    def __init__(self):
        self.statut = 0
        self.attendus = 0
        self.octets = []
        self.sysex = False

    def lire(self, donnees):
        for o in donnees:
            if o >= 0xF8:                 # temps réel (horloge, active sensing) : ignoré
                continue
            if o >= 0x80:
                self.sysex = (o == 0xF0)
                if o >= 0xF0:             # SysEx, messages système : ignorés
                    self.statut, self.octets = 0, []
                    continue
                self.statut, self.attendus, self.octets = o, DONNEES[o & 0xF0], []
                continue
            if self.sysex or not self.statut:
                continue
            self.octets.append(o)
            if len(self.octets) == self.attendus:
                d = self.octets + [0]
                self.octets = []
                yield self.statut, d[0], d[1]


# --- Port série à 31 250 bauds ----------------------------------------------------
# termios ne connaît que les vitesses standard : on passe par termios2 (BOTHER),
# que le pilote PL011 du Pi accepte.
TCGETS2, TCSETS2 = 0x802C542A, 0x402C542B
CBAUD, BOTHER = 0o010017, 0o010000
FORMAT_TERMIOS2 = "=IIIIB19sII"


def ouvrir_serie(chemin, bauds=31250):
    fd = os.open(chemin, os.O_RDONLY | os.O_NOCTTY)
    try:
        tty.setraw(fd)
        buf = array.array("B", bytes(struct.calcsize(FORMAT_TERMIOS2)))
        fcntl.ioctl(fd, TCGETS2, buf, True)
        i, o, c, l, ligne, cc, _, _ = struct.unpack(FORMAT_TERMIOS2, buf.tobytes())
        c = (c & ~CBAUD) | BOTHER | termios.CREAD | termios.CLOCAL
        fcntl.ioctl(fd, TCSETS2, struct.pack(FORMAT_TERMIOS2, i, o, c, l, ligne, cc, bauds, bauds))
    except Exception:
        os.close(fd)
        raise
    return fd


# --- Les sources -----------------------------------------------------------------

def nom_carte(chemin):
    """/dev/snd/midiC1D0 -> nom de la carte ALSA (« M4 », « UM-ONE »…)."""
    base = os.path.basename(chemin)
    try:
        carte, dev = base[5:].split("D")
        with open(f"/proc/asound/card{carte}/id") as f:
            nom = f.read().strip()
        return nom if dev == "0" else f"{nom} {int(dev) + 1}"
    except (OSError, ValueError):
        return base


class EntreesMidi:
    def __init__(self, rappel, uart="", journal=print):
        self.rappel = rappel
        self.uart = uart
        self.journal = journal
        self.ouvertes = {}            # chemin -> nom affiché
        self.verrou = threading.Lock()
        self.refus = {}               # chemin -> heure du dernier échec (pas de spam)

    def ports(self):
        with self.verrou:
            return sorted(self.ouvertes.values())

    def demarrer(self):
        threading.Thread(target=self._surveiller, daemon=True).start()

    def _surveiller(self):
        while True:
            chemins = glob.glob("/dev/snd/midiC*D*")
            if self.uart:
                chemins.append(self.uart)
            for ch in chemins:
                with self.verrou:
                    if ch in self.ouvertes:
                        continue
                if time.time() - self.refus.get(ch, 0) < 30:
                    continue
                try:
                    if ch == self.uart:
                        fd, nom = ouvrir_serie(ch), "Entrée MIDI DIN"
                    else:
                        fd, nom = os.open(ch, os.O_RDONLY), nom_carte(ch)
                except OSError as e:
                    self.refus[ch] = time.time()
                    self.journal(f"MIDI : {ch} inutilisable ({e.strerror})")
                    continue
                with self.verrou:
                    self.ouvertes[ch] = nom
                self.journal(f"MIDI : {nom} branché ({ch})")
                threading.Thread(target=self._lire, args=(ch, fd, nom), daemon=True).start()
            time.sleep(2)

    def _lire(self, chemin, fd, nom):
        analyseur = Analyseur()
        try:
            while True:
                donnees = os.read(fd, 256)
                if not donnees:
                    break
                for statut, d1, d2 in analyseur.lire(donnees):
                    try:
                        self.rappel(nom, statut, d1, d2)
                    except Exception as e:      # un bug de traitement ne coupe pas l'entrée
                        self.journal(f"MIDI : erreur de traitement ({e})")
        except OSError:
            pass
        finally:
            os.close(fd)
            with self.verrou:
                self.ouvertes.pop(chemin, None)
            self.journal(f"MIDI : {nom} débranché")


def decrire(statut, d1, d2):
    t, canal = statut & 0xF0, (statut & 0x0F) + 1
    if t == 0xB0:
        return f"Canal {canal} · CC {d1} = {d2}"
    if t == 0xC0:
        return f"Canal {canal} · Program Change {d1}"
    if t == 0x90 and d2:
        return f"Canal {canal} · Note {d1}"
    return f"Canal {canal} · message {t:#x}"


if __name__ == "__main__":
    import sys
    e = EntreesMidi(lambda nom, s, a, b: print(f"{nom} : {decrire(s, a, b)}", flush=True),
                    uart=sys.argv[1] if len(sys.argv) > 1 else "")
    e.demarrer()
    print("Lecture des entrées MIDI (Ctrl+C pour arrêter)…", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
