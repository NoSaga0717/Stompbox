# Tête NAM sur Raspberry Pi 5 (moteur stompbox)

Montage : guitare → MOTU M4 (entrée 1, instrument) → Pi 5 (USB) → M4 sortie 1 → power amp → 4x12. Écran tactile 7" DSI sur le Pi.

Logiciel : Raspberry Pi OS Lite 64 bits, JACK (serveur audio temps réel) et **stompbox-jack**, le moteur audio de ce dépôt : il contient NAM et tous les effets (compresseur, drive, délai, réverbe, chorus, trémolo…) compilés en dur. Plus de mod-host ni de plugin LV2. Tout démarre tout seul à l'allumage, et l'interface tactile s'affiche sur l'écran de la tête.

La même interface est disponible à trois endroits, qui voient et changent exactement la même chose :

- sur l'**écran tactile** de la tête ;
- dans un **navigateur** (ordinateur, téléphone, tablette) : http://tetenam.local:8080 ;
- dans l'**appli Android**, en Bluetooth, sans Wi-Fi.

Ce qui tournait sur le Pi 3 (dépôt `neural-amp-modeler-lv2`, dossier `tete-nam`) est remplacé par ce dossier.

## 1. Préparer la carte SD (sur votre ordinateur)

1. Installez **Raspberry Pi Imager** : https://www.raspberrypi.com/software/
2. **Appareil** : Raspberry Pi 5.
3. **Système** : *Raspberry Pi OS (other)* → **Raspberry Pi OS Lite (64-bit)**. Lite = sans bureau, c'est voulu : l'interface tactile n'a pas besoin du bureau.
4. **Stockage** : votre carte SD (32 Go, classe A2 de préférence).
5. Personnalisation :
   - nom d'hôte : `tetenam`
   - utilisateur et mot de passe (par exemple `alex`)
   - Wi-Fi : nom du réseau, mot de passe, pays **FR**
   - fuseau horaire Europe/Paris, clavier `fr`
   - **SSH activé**, avec authentification par mot de passe
6. Écrivez la carte, mettez-la dans le Pi, branchez l'alim. Le premier démarrage prend 1 à 2 minutes.

Matériel à prévoir :

- **Alimentation officielle 27 W (5 V 5 A)**. Avec une alim plus faible, le Pi 5 limite le courant de ses ports USB et la M4 (alimentée par l'USB) peut décrocher.
- **Refroidissement** : le ventilateur officiel (Active Cooler) ou un boîtier ventilé. Le processeur tourne en permanence à fond pour l'audio.
- **Écran** : nappe DSI 22 broches vers 15 broches (le connecteur du Pi 5 est plus petit que celui des écrans). Branchez-la sur le connecteur **CAM/DISP 1** (sinon, voir l'option `--ecran-dsi0` plus bas).

## 2. Se connecter au Pi

Depuis un terminal (Windows : PowerShell, Mac/Linux : Terminal) :

```bash
ssh alex@tetenam.local
```

(Remplacez `alex` par votre utilisateur. Si `tetenam.local` ne répond pas, cherchez l'adresse IP du Pi sur votre box.)

## 3. Installer (copier-coller, une ligne à la fois)

Branchez la M4 et l'écran avant de commencer.

```bash
sudo apt update && sudo apt install -y git
git clone https://github.com/NoSaga0717/stompbox.git
cd stompbox
sudo ./tete-nam/install.sh
sudo reboot
```

Le script installe JACK, Chromium (pour l'écran), compile le moteur sur le Pi, règle le temps réel, l'écran et le démarrage automatique. **Comptez 15 à 25 minutes** la première fois (téléchargements, puis 5 à 10 minutes de compilation).

Options :

| Option | Effet |
|---|---|
| `--sans-ecran` | Pas d'interface sur l'écran de la tête (page web et appli seulement) |
| `--ecran-dsi0` | Écran branché sur le connecteur CAM/DISP 0 au lieu du 1 |
| `--generique` | Compile sans les optimisations propres au processeur du Pi 5 (à utiliser si la carte SD doit servir sur un autre modèle de Pi) |

**Mettre à jour** : `cd stompbox && git pull && sudo ./tete-nam/install.sh`. Seul ce qui a changé est recompilé ; réglages, presets et modèles sont gardés. Si la connexion SSH coupe pendant l'installation, reconnectez-vous et relancez la même commande.

**Carte SD venant du Pi 3** : ça marche aussi. L'ancienne chaîne Pédale → Ampli est reprise automatiquement comme premier état de la nouvelle chaîne, et `/etc/tete-nam.conf` est converti (copie de sécurité gardée dans `/etc`).

## 4. Copier les modèles

Sur la page web (navigateur), touchez la zone **DSP** en haut à droite puis **Envoyer des fichiers .nam vers la tête**. Vous pouvez en envoyer plusieurs d'un coup. Les espaces dans les noms sont remplacés par des `_`. L'appli Android a le même bouton.

En ligne de commande, depuis votre ordinateur :

```bash
scp *.nam alex@tetenam.local:nam/
```

Les modèles sont dans `~/nam` sur le Pi. Un modèle copié à la main apparaît dans la liste au prochain choix de modèle.

## 5. L'interface

- **En haut** : numéro et nom du preset, ◀ ▶ pour passer au précédent ou au suivant, **Enregistrer**, l'accordeur (♪) et la charge DSP (toucher pour les détails et les réglages système).
- **Au milieu, la chaîne** : la guitare entre à gauche (IN), le son sort à droite (OUT). Chaque bloc est une pédale ou un NAM.
  - toucher un bloc : ses réglages s'affichent en dessous ;
  - glisser sur la chaîne : la faire défiler ;
  - **appui long sur un bloc puis glisser** : le déplacer ailleurs dans la chaîne ;
  - **+** : ajouter un bloc après le bloc sélectionné.
- **En bas, les réglages du bloc** : faire glisser un bouton vers le haut ou la droite pour monter, double toucher pour revenir à la valeur par défaut. Les boutons en gris sont les réglages avancés. **Activé / Bypass** coupe le bloc sans le retirer.
- Bloc NAM : choix du modèle, **Normalisé** (tous les modèles sortent au même volume) ou **Calibré** (le modèle sort au niveau du vrai matériel, utile pour une pédale avant un ampli), entrée, sortie et qualité (taille du modèle A2 qui tourne, 1 = complet).

On peut mettre autant de blocs NAM et d'effets qu'on veut, dans n'importe quel ordre (16 blocs au maximum). La limite réelle est la charge DSP.

**Presets** : toucher le nom du preset ouvre la liste (charger, supprimer, nouveau). **Enregistrer** écrase le preset actuel ou en crée un nouveau. Les modifications non enregistrées sont gardées même après une coupure de courant : la tête redémarre comme vous l'avez laissée, avec « modifié » affiché.

**Système** (toucher la zone DSP) : courbe de charge, xruns, température, taille du buffer JACK (changer relance l'audio, 5 secondes de silence), calibration d'entrée (16 dBu pour la M4) et volume général.

## 6. Vérifier que tout va bien

```bash
tete-nam etat
```

Repères :

- **Charge DSP** sous 70 % : confortable. Au-delà de 85 %, ça finira par craquer. Rappel : JACK traite toute la chaîne sur **un seul cœur**.
- **Xruns** : 0 en jouant 5 minutes. Le compteur repart à zéro à chaque redémarrage du moteur.
- **Température** sous 80 °C.
- **Throttling** `0x0`. Autre chose = alimentation trop faible ou surchauffe.

Autres commandes : `tete-nam presets` (liste), `tete-nam preset "Nom"` (charger), `tete-nam cpu`.

### Sans Wi-Fi (studio) : le réseau de la tête

Au démarrage, la tête attend jusqu'à 45 secondes un Wi-Fi qu'elle connaît. Si elle n'en trouve aucun, elle crée **son propre réseau Wi-Fi** :

1. Sur le téléphone, connectez-vous au Wi-Fi **TeteNAM** (mot de passe `tetenam2026` par défaut, à changer).
2. Ouvrez **http://tetenam.local:8080**. Si le nom ne répond pas, ouvrez **http://10.42.0.1:8080**.
3. En SSH : `ssh alex@10.42.0.1`.

Changer le nom, le mot de passe ou le comportement : `sudo nano /etc/tete-nam-wifi.conf` (`MODE` = `auto`, `hotspot` ou `maison` ; `SSID`, `MOT_DE_PASSE`, `CANAL`). Commandes : `tete-nam-wifi etat`, `tete-nam-wifi hotspot`, `tete-nam-wifi maison`.

## 7. Appli Android (Bluetooth)

L'appli **Tête NAM** se connecte en Bluetooth Low Energy et affiche **la même interface que l'écran de la tête** : elle la demande à la tête à la connexion (et la garde en mémoire), puis lui fait passer tous ses réglages en Bluetooth. Une mise à jour de la tête met donc aussi l'appli à jour, sans rien réinstaller.

1. Sur le téléphone, ouvrez https://github.com/NoSaga0717/stompbox/releases/tag/appli-android et touchez **TeteNAM.apk**. Android demande d'autoriser l'installation depuis le navigateur : acceptez (une seule fois). **Désinstallez d'abord l'ancienne appli** (version Pi 3) : la nouvelle est signée avec une autre clé, Android refuse de l'installer par-dessus.
2. Ouvrez l'appli, acceptez l'autorisation « Appareils à proximité ». Elle trouve la tête toute seule (nom Bluetooth `TeteNAM`), aucun appairage à faire.

- Une seule appli connectée à la fois. Portée : une dizaine de mètres.
- Le Bluetooth est plus lent que le Wi-Fi : l'interface se met à jour toutes les 1,5 seconde au lieu de chaque seconde. L'envoi de fichiers .nam est lent (environ une minute par Mo).
- Android 11 et moins : la localisation du téléphone doit être activée pour la recherche Bluetooth (exigence d'Android).
- Journal du pont Bluetooth sur la tête : `journalctl -u tete-nam-ble -f`.

L'APK est reconstruit automatiquement par GitHub à chaque modification de `tete-nam/android/`. La clé de signature n'est pas dans le dépôt (il est public) : elle est dans deux secrets du dépôt, `TETE_NAM_KEYSTORE` (le fichier de clé en base64) et `TETE_NAM_KEYSTORE_MDP` (son mot de passe), à créer une fois dans *Settings → Secrets and variables → Actions*. Sans eux, l'APK est signé avec une clé jetable et chaque mise à jour demande de désinstaller l'appli.

## 8. Pédalier MIDI

La tête lit toute seule **toutes les entrées MIDI branchées** : la prise **MIDI IN de la MOTU M4**, un câble USB-MIDI, un pédalier USB. Rien à installer ni à régler, un appareil branché à chaud est vu en 2 secondes.

**Test avec le FCB1010 et la M4 :** câble MIDI de la prise **MIDI OUT** du FCB1010 vers la prise **MIDI IN** de la M4. Sur l'écran : **DSP** (en haut à droite) → **Réglages MIDI…**. La M4 doit apparaître dans « Entrées MIDI détectées », et chaque appui sur un pied s'affiche dans « Dernier message reçu » (le mot MIDI clignote aussi en haut à droite).

- **Changer de preset :** chaque pied du FCB1010 envoie un *Program Change*. Program Change 0 charge le preset 1 de la liste, 1 le preset 2, etc. (le numéro affiché en haut de l'écran).
- **Allumer / éteindre un effet :** toucher le bloc, **MIDI**, **Apprendre** sur « Allumé / éteint », puis appuyer sur le pied. Le FCB1010 d'origine envoie toujours la même valeur : laisser « Chaque appui bascule ». Pour le FCB, régler sur le pied un *Control Change* avec la valeur 127 (une valeur 0 est ignorée).
- **Pédale d'expression :** même chose sur un bouton (par exemple « Pédale » d'un bloc Wah, ou « Niveau » d'un bloc Volume) puis bouger la pédale : elle fait varier le réglage du minimum au maximum.
- **Preset suivant / précédent, accordeur :** dans **Réglages MIDI…**, valables pour tous les presets.
- Les assignations des blocs sont **enregistrées avec le preset** (penser à Enregistrer). Un petit badge `CC 20` sur un bouton montre qu'il est piloté au pied.
- **Canal MIDI :** « Tous les canaux » par défaut ; à régler si le pédalier pilote aussi d'autres appareils.
- Assignations rangées dans `~/tete-nam/midi.json`. Journal : `journalctl -u tete-nam-web -f` (branchement et débranchement des entrées).
- Les entrées MIDI sont ouvertes par la page web : un autre programme (`amidi`, `aseqdump`) ne peut pas les lire en même temps. Pour essayer à la main : `sudo systemctl stop tete-nam-web` puis `python3 /opt/tete-nam/midi.py`.

**Entrée MIDI DIN sur le port série (carte maison) :** `MIDI_UART=/dev/ttyAMA0` dans `/etc/tete-nam.conf`, UART activé dans `/boot/firmware/config.txt` (`dtparam=uart0=on`) et console série retirée de `cmdline.txt`. Lu à 31 250 bauds. Pas encore essayé sur un vrai Pi.

## Réglages (`/etc/tete-nam.conf`)

La chaîne et les presets ne sont pas dans ce fichier : ils sont dans `~/tete-nam` (presets dans `~/tete-nam/Presets`, état en cours dans `~/tete-nam/en-cours.txt`).

| Réglage | Rôle |
|---|---|
| `CARD` | Nom de la carte son (`cat /proc/asound/cards` pour le vérifier) |
| `RATE` | 48000, comme les modèles |
| `PERIOD`, `NPERIODS` | Taille (64 à 512) et nombre (2) de buffers JACK |
| `INPUT`, `OUTPUTS` | Entrée guitare et sorties de la carte son (ports JACK) |
| `INPUT_DBU` | Calibration d'entrée : dBu pour 0 dBFS (16 pour la M4) |
| `VOLUME_DB` | Volume général en sortie |
| `ECRAN` | `oui` ou `non` : interface sur l'écran de la tête |
| `ECRAN_ROTATION` | 0, 90, 180 ou 270 (écran monté à l'envers : 180) |
| `MIDI_UART` | Port série de l'entrée MIDI DIN (vide = aucun ; l'USB et la M4 sont toujours lus) |

Après une modification à la main : `sudo systemctl restart tete-nam` (audio) ou `sudo systemctl restart tete-nam-ecran` (écran).

## Comment c'est organisé

| Service | Programme | Rôle |
|---|---|---|
| `tete-nam` | `run.sh` | JACK puis le moteur `stompbox-jack` |
| `tete-nam-web` | `web.py` | Page web et API, seul programme qui parle au moteur (port 24639) |
| `tete-nam-ecran` | `ecran.sh` | Chromium en plein écran sur l'écran tactile (via `cage`) |
| `tete-nam-ble` | `ble.py` | Pont Bluetooth vers la page web, pour l'appli |
| `tete-nam-wifi` | `wifi-secours.sh` | Réseau Wi-Fi de secours |

Fichiers installés dans `/opt/tete-nam`. Le moteur travaille dans `~/tete-nam` (presets, lien `NAM` vers `~/nam`). La disposition des chaînes du moteur est dans `chaine-init.txt` : accordeur en entrée, la chaîne `Main` (celle de l'interface et des presets), volume général en sortie.

## Si ça ne marche pas

- `tete-nam etat` dit *inactive* ou *activating* : `journalctl -u tete-nam -n 50`. Les messages commencent par ERREUR et disent quoi faire.
- **Carte introuvable** : `cat /proc/asound/cards`, puis mettez dans `CARD=` le nom entre crochets de la M4.
- **Pas de son mais tout est actif** : vérifiez le bouton MONITOR de la M4, le câble vers le power amp et `jack_lsp -c` (`system:capture_1` doit aller vers `stompbox:input_1`, et `stompbox:output_1` vers `system:playback_1`).
- **Écran noir** : `journalctl -u tete-nam-ecran -n 50`. Vérifiez la nappe (sens et connecteur DISP 1) et la ligne `dtoverlay=vc4-kms-dsi-7inch` à la fin de `/boot/firmware/config.txt`. Écran sur DISP 0 : relancez `sudo ./tete-nam/install.sh --ecran-dsi0`.
- **Le tactile ne correspond pas à l'image** (écran tourné) : réglez `ECRAN_ROTATION` dans `/etc/tete-nam.conf`.
- **Console texte sur l'écran au lieu de l'interface** : Ctrl+Alt+F2 permet d'y basculer ; `sudo systemctl restart tete-nam-ecran` relance l'interface.
- **Annuler les réglages de démarrage** : copies d'origine dans `/boot/firmware/cmdline.txt.avant-tete-nam` et `/boot/firmware/config.txt.avant-tete-nam`.
- **Couper le démarrage automatique** : `sudo systemctl disable --now tete-nam tete-nam-ecran`.
- **Le réseau TeteNAM n'apparaît pas** : `journalctl -u tete-nam-wifi -n 30`, et vérifiez le pays Wi-Fi (`sudo raspi-config`, *Localisation Options*, *WLAN Country*, FR).
