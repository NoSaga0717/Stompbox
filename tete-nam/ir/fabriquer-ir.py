#!/usr/bin/env python3
"""Fabrique l'IR de baffle de base de la Tête NAM (Baffle-4x12-base.wav).

Ce n'est pas la mesure d'une vraie baffle : c'est une courbe « 4x12 fermée,
haut-parleurs type V30, micro dynamique devant » dessinée à la main, puis
transformée en IR à phase minimale. Elle sert à écouter tout de suite au
casque ou sur des enceintes de studio. Une vraie IR mesurée (achetée ou
gratuite) sonnera mieux : il suffit de l'envoyer sur la tête.

Libre de droits (fabriquée ici). Relancer : python3 fabriquer-ir.py
"""
import os
import wave

import numpy as np

FREQ = 48000
TAILLE = 2048          # 43 ms : le bloc Baffle n'en garde pas plus
N_FFT = 1 << 15

# (Hz, dB) : grave coupé sous la résonance de la baffle, bosse vers 110 Hz,
# creux léger dans le bas-médium, présence vers 2,5 kHz, chute forte après 5 kHz
COURBE = [(20, -40), (45, -22), (70, -8), (95, 1), (115, 2.5), (150, 1), (250, 0),
          (400, -2), (700, -0.5), (1000, 0), (1500, -1.5), (2000, 1.5), (2600, 4),
          (3300, 2.5), (4200, 0), (5000, -4), (6000, -11), (7000, -18), (8000, -25),
          (10000, -35), (13000, -48), (24000, -70)]


def ir_phase_minimale(gain_db):
    """IR à phase minimale (méthode du cepstre) pour un module donné en dB."""
    module = 10 ** (gain_db / 20)
    spectre_log = np.log(np.maximum(module, 1e-7))
    cepstre = np.fft.irfft(spectre_log, N_FFT)
    plie = np.zeros(N_FFT)
    plie[0] = cepstre[0]
    plie[1:N_FFT // 2] = 2 * cepstre[1:N_FFT // 2]
    plie[N_FFT // 2] = cepstre[N_FFT // 2]
    return np.fft.irfft(np.exp(np.fft.rfft(plie)), N_FFT)


def main():
    f = np.fft.rfftfreq(N_FFT, 1 / FREQ)
    fc, gc = zip(*COURBE)
    gain_db = np.interp(np.log10(np.maximum(f, 1)), np.log10(fc), gc)
    ir = ir_phase_minimale(gain_db)[:TAILLE]
    ir *= 0.5 * (1 + np.cos(np.linspace(0, np.pi, TAILLE))) ** 0.5   # fin en douceur
    ir *= 0.89 / np.max(np.abs(ir))                                  # crête à -1 dBFS
    pcm = np.round(ir * (2 ** 23 - 1)).astype(np.int32)
    octets = b"".join(int(v).to_bytes(4, "little", signed=True)[:3] for v in pcm)
    sortie = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Baffle-4x12-base.wav")
    with wave.open(sortie, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(3)
        w.setframerate(FREQ)
        w.writeframes(octets)
    print("Écrit :", sortie)


if __name__ == "__main__":
    main()
