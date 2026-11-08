#!/usr/bin/env python3
"""Client TONE3000 de la Tête NAM (https://www.tone3000.com/api).

La tête se connecte au compte TONE3000 par un code à valider sur un
téléphone (« device flow ») : l'écran montre un QR code et un code
XXXX-XXXX, on le valide sur tone3000.com/activate, et la tête garde ses
jetons dans ~/tete-nam/tone3000/jetons.json. Rien à taper sur la tête.

La clé de l'application (t3k_pub_..., créée sur tone3000.com, Settings ->
API Keys) est lue dans /etc/tete-nam.conf (T3K_CLE). Elle n'est pas
secrète : c'est l'identifiant de l'application, pas un mot de passe.

Règles de l'API suivies ici :
  - toujours demander architecture=2 (sinon les modèles A2 sont exclus) ;
  - chaque modèle se télécharge par son model_url avec le jeton ;
  - jeton rafraîchi 60 s avant la fin, une seule fois à la fois, et une
    nouvelle tentative après une réponse 401 ;
  - 100 requêtes par minute (la recherche est plus limitée).

Uniquement la bibliothèque standard de Python.
"""
import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

API = os.environ.get("TETE_NAM_T3K_API", "https://www.tone3000.com").rstrip("/")
TAILLE_MAX_MODELE = 50 * 1024 * 1024
GEARS = ("amp", "amp-cab", "pedal", "outboard", "cab", "space", "experimental")
LISTES = ("favorited", "downloaded", "created", "trending", "latest", "search")
TRIS = ("best-match", "trending", "newest", "downloads-all-time")


class ErreurT3K(Exception):
    pass


class NonConnecte(ErreurT3K):
    pass


def _texte_erreur(code, corps):
    try:
        d = json.loads(corps)
        return d.get("error_description") or d.get("message") or d.get("error") or f"erreur {code}"
    except ValueError:
        return f"erreur {code}"


class Client:
    def __init__(self, dossier, cle, version="dev", journal=print):
        """cle : fonction qui renvoie la clé t3k_pub_ actuelle (lue dans la conf)."""
        self.dossier = dossier
        self.fichier_jetons = os.path.join(dossier, "jetons.json")
        self.cle = cle
        self.agent = f"TeteNAM/{version}"
        self.journal = journal
        self.verrou = threading.Lock()          # jetons
        self.verrou_rafraichir = threading.Lock()
        self.jetons = None
        self.utilisateur = None
        self.connexion = None                   # code en cours de validation
        self.rev = 0                            # +1 à chaque changement de connexion
        self._lire()

    # --- Jetons -------------------------------------------------------------

    def _lire(self):
        try:
            with open(self.fichier_jetons) as f:
                d = json.load(f)
            if d.get("access_token") and d.get("refresh_token"):
                self.jetons = d
                self.utilisateur = d.get("utilisateur")
        except (OSError, ValueError):
            pass

    def _ecrire(self):
        os.makedirs(self.dossier, exist_ok=True)
        tmp = self.fichier_jetons + ".tmp"
        if self.jetons is None:
            try:
                os.remove(self.fichier_jetons)
            except OSError:
                pass
            return
        d = dict(self.jetons, utilisateur=self.utilisateur)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(d, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.fichier_jetons)

    def _garder_jetons(self, r):
        jetons = {"access_token": r["access_token"],
                  "refresh_token": r.get("refresh_token") or (self.jetons or {}).get("refresh_token", ""),
                  "expire": time.time() + float(r.get("expires_in") or 3600)}
        with self.verrou:
            self.jetons = jetons
            self._ecrire()

    def connecte(self):
        return self.jetons is not None

    def deconnecter(self):
        with self.verrou:
            self.jetons, self.utilisateur, self.connexion = None, None, None
            self._ecrire()
        self.rev += 1

    # --- HTTP ----------------------------------------------------------------

    def _http(self, methode, chemin, form=None, jeton=None, delai=20, max_octets=4 * 1024 * 1024):
        url = chemin if chemin.startswith("http") else API + chemin
        data = urllib.parse.urlencode(form).encode() if form is not None else None
        entetes = {"User-Agent": self.agent, "Accept": "application/json"}
        if data is not None:
            entetes["Content-Type"] = "application/x-www-form-urlencoded"
        if jeton:
            entetes["Authorization"] = "Bearer " + jeton
        req = urllib.request.Request(url, data=data, headers=entetes, method=methode)
        try:
            with urllib.request.urlopen(req, timeout=delai) as r:
                corps = r.read(max_octets + 1)
                if len(corps) > max_octets:
                    raise ErreurT3K("Réponse trop grosse")
                dep = r.headers.get("X-Tone3000-Deprecations")
                if dep:
                    self.journal(f"TONE3000 : paramètre déprécié sur {chemin} : {dep}")
                return r.status, corps
        except urllib.error.HTTPError as e:
            return e.code, e.read(65536)
        except (urllib.error.URLError, OSError) as e:
            raise ErreurT3K("TONE3000 injoignable : la tête est-elle connectée à Internet ? "
                            f"({getattr(e, 'reason', e)})")

    def _cle(self):
        c = (self.cle() or "").strip()
        if not c:
            raise ErreurT3K("Clé TONE3000 manquante (T3K_CLE, page Système)")
        return c

    def _jeton(self, forcer=False):
        if self.jetons is None:
            raise NonConnecte("Pas connecté à TONE3000")
        if not forcer and time.time() < self.jetons["expire"] - 60:
            return self.jetons["access_token"]
        with self.verrou_rafraichir:                       # un seul rafraîchissement à la fois
            j = self.jetons
            if j is None:
                raise NonConnecte("Pas connecté à TONE3000")
            if not forcer and time.time() < j["expire"] - 60:
                return j["access_token"]
            code, corps = self._http("POST", "/api/v1/oauth/token", {
                "grant_type": "refresh_token", "refresh_token": j["refresh_token"],
                "client_id": self._cle()})
            if code in (400, 401):                         # session terminée
                self.journal("TONE3000 : session expirée, reconnexion nécessaire")
                self.deconnecter()
                raise NonConnecte("Session TONE3000 expirée : reconnectez la tête")
            if code != 200:
                raise ErreurT3K("TONE3000 : " + _texte_erreur(code, corps))
            self._garder_jetons(json.loads(corps))
            return self.jetons["access_token"]

    def _get(self, chemin, params=None, max_octets=4 * 1024 * 1024, brut=False):
        if params:
            p = {k: v for k, v in params.items() if v not in (None, "", False)}
            if p:
                chemin += ("&" if "?" in chemin else "?") + urllib.parse.urlencode(p)
        code, corps = self._http("GET", chemin, jeton=self._jeton(), max_octets=max_octets)
        if code == 401:
            code, corps = self._http("GET", chemin, jeton=self._jeton(forcer=True), max_octets=max_octets)
        if code == 401:
            self.deconnecter()
            raise NonConnecte("Session TONE3000 expirée : reconnectez la tête")
        if code == 429:
            raise ErreurT3K("TONE3000 : trop de demandes, réessayez dans une minute")
        if code == 403:
            raise ErreurT3K("TONE3000 : accès refusé à cette fonction pour cette clé")
        if code == 404:
            raise ErreurT3K("TONE3000 : introuvable (tone privé ou supprimé ?)")
        if code != 200:
            raise ErreurT3K("TONE3000 : " + _texte_erreur(code, corps))
        if brut:
            return corps
        try:
            return json.loads(corps)
        except ValueError:
            raise ErreurT3K("TONE3000 : réponse illisible")

    # --- Connexion par code (device flow, RFC 8628) -------------------------

    def demarrer_connexion(self):
        code, corps = self._http("POST", "/api/v1/oauth/device_authorization", {"client_id": self._cle()})
        if code != 200:
            raise ErreurT3K("TONE3000 : " + _texte_erreur(code, corps))
        d = json.loads(corps)
        if not d.get("device_code") or not d.get("user_code"):
            raise ErreurT3K("TONE3000 : réponse de connexion incomplète")
        c = {"etat": "attente", "code": d["user_code"],
             "url": d.get("verification_uri") or API + "/activate",
             "url_complete": d.get("verification_uri_complete") or d.get("verification_uri") or API + "/activate",
             "fin": time.time() + float(d.get("expires_in") or 600),
             "intervalle": max(1.0, float(d.get("interval") or 5)), "message": ""}
        self.connexion = c
        self.rev += 1
        threading.Thread(target=self._attendre_validation, args=(c, d["device_code"]), daemon=True).start()
        return c

    def annuler_connexion(self):
        if self.connexion and self.connexion["etat"] == "attente":
            self.connexion["etat"] = "annule"
        self.connexion = None
        self.rev += 1

    def _attendre_validation(self, c, device_code):
        while c["etat"] == "attente" and self.connexion is c:
            time.sleep(c["intervalle"])
            if c["etat"] != "attente" or self.connexion is not c:
                return
            if time.time() > c["fin"]:
                c.update(etat="expire", message="Code expiré : recommencez")
                break
            try:
                code, corps = self._http("POST", "/api/v1/oauth/token", {
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device_code, "client_id": self._cle()})
            except ErreurT3K as e:
                c["message"] = str(e)
                continue
            if code == 200:
                self._garder_jetons(json.loads(corps))
                self.lire_utilisateur()
                c.update(etat="ok", message="Connecté")
                self.journal("TONE3000 : tête connectée au compte",
                             (self.utilisateur or {}).get("username", "?"))
                break
            try:
                err = json.loads(corps).get("error", "")
            except ValueError:
                err = ""
            if err == "authorization_pending":
                continue
            if err == "slow_down":
                c["intervalle"] += 5
                continue
            c.update(etat="echec", message={"access_denied": "Connexion refusée sur le téléphone",
                                            "expired_token": "Code expiré : recommencez"}.get(
                err, "Connexion impossible : " + (err or f"erreur {code}")))
            break
        self.rev += 1

    def adresse_catalogue(self, redirect_uri):
        """Page « Select » de TONE3000 (tout le catalogue, sur le téléphone) :
        la personne choisit un tone, TONE3000 renvoie code + tone_id à
        redirect_uri (adresse de la tête sur le réseau local). PKCE S256."""
        verificateur = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
        defi = base64.urlsafe_b64encode(hashlib.sha256(verificateur.encode()).digest()).rstrip(b"=").decode()
        etat = secrets.token_urlsafe(16)
        q = {"client_id": self._cle(), "redirect_uri": redirect_uri, "response_type": "code",
             "code_challenge": defi, "code_challenge_method": "S256", "state": etat,
             "prompt": "select_tone", "gears": "amp_amp-cab_pedal_outboard", "format": "nam",
             "architecture": "2", "menubar": "true"}
        return API + "/api/v1/oauth/authorize?" + urllib.parse.urlencode(q), etat, verificateur

    def connexion_par_code(self, code, verificateur, redirect_uri):
        """Connexion arrivée par la page TONE3000 (catalogue sur le téléphone)."""
        c, corps = self._http("POST", "/api/v1/oauth/token", {
            "grant_type": "authorization_code", "code": code, "code_verifier": verificateur,
            "redirect_uri": redirect_uri, "client_id": self._cle()})
        if c != 200:
            raise ErreurT3K("TONE3000 : " + _texte_erreur(c, corps))
        self._garder_jetons(json.loads(corps))
        if not self.utilisateur:
            self.lire_utilisateur()
        self.rev += 1

    def lire_utilisateur(self):
        try:
            u = self._get("/api/v1/user")
            self.utilisateur = {"username": u.get("username", ""), "nom": u.get("display_name") or u.get("username", ""),
                                "avatar": u.get("avatar_url") or ""}
            with self.verrou:
                self._ecrire()
        except ErreurT3K as e:
            self.journal("TONE3000 : profil illisible :", e)

    def etat(self):
        c = self.connexion
        if c and c["etat"] == "attente" and time.time() > c["fin"]:
            c.update(etat="expire", message="Code expiré : recommencez")
        return {"connecte": self.connecte(), "utilisateur": self.utilisateur,
                "connexion": None if not c else {k: c[k] for k in ("etat", "code", "url", "url_complete", "message")}
                | {"reste": max(0, int(c["fin"] - time.time()))}}

    # --- Catalogue -----------------------------------------------------------

    def liste(self, quoi, texte="", gear="", page=1, tri="", taille=20):
        """Une page de tones : favoris, téléchargés, créés, tendances, nouveautés
        ou recherche. Renvoie {"tones": [...], "page", "pages", "total", "masques"}."""
        if quoi not in LISTES:
            raise ErreurT3K("Liste inconnue")
        gear = gear if gear in GEARS else ""
        page = max(1, int(page or 1))
        if quoi == "search":
            d = self._get("/api/v1/tones/search", {
                "query": texte[:100], "gears": gear, "format": "nam", "architecture": "2",
                "sort": tri if tri in TRIS else ("best-match" if texte else "trending"),
                "page": page, "page_size": taille})
        elif quoi == "trending":
            d = self._get("/api/v1/tones/trending", {"gear": gear})
        elif quoi == "latest":
            d = self._get("/api/v1/tones/latest")
        else:
            d = self._get(f"/api/v1/tones/{quoi}", {"query": texte[:100], "gear": gear,
                                                    "page": page, "page_size": taille})
        tones = [resume_tone(t) for t in d.get("data", [])]
        gardes = [t for t in tones if t["format"] == "nam" and t["a2"] != 0]
        return {"tones": gardes, "page": d.get("page", 1), "pages": d.get("total_pages", 1),
                "total": d.get("total", len(tones)), "masques": len(tones) - len(gardes)}

    def tone(self, tone_id):
        tone_id = int(tone_id)
        t = self._get(f"/api/v1/tones/{tone_id}", {"architecture": "2"})
        m = self._get("/api/v1/models", {"tone_id": tone_id, "architecture": "2", "page": 1, "page_size": 100})
        modeles = [{"id": x["id"], "nom": x.get("name") or f"Modèle {x['id']}", "taille": x.get("size") or "",
                    "url": x.get("model_url") or "", "architecture": x.get("architecture_version")}
                   for x in m.get("data", []) if x.get("model_url")]
        r = resume_tone(t)
        r["description"] = (t.get("description") or "")[:600]
        r["licence"] = t.get("license") or ""
        r["images"] = [i for i in (t.get("images") or []) if isinstance(i, str)][:4]
        return r, modeles

    def telecharger(self, model_url):
        """Contenu d'un fichier .nam (le jeton est obligatoire pour model_url)."""
        if not model_url.startswith(API + "/"):
            raise ErreurT3K("Adresse de modèle inattendue")
        return self._get(model_url, max_octets=TAILLE_MAX_MODELE, brut=True)


def resume_tone(t):
    """Ce que l'interface affiche d'un tone (court, pour le Bluetooth)."""
    u = t.get("user") or {}
    images = [i for i in (t.get("images") or []) if isinstance(i, str)]
    return {"id": t.get("id"), "titre": t.get("title") or "Sans titre", "gear": t.get("gear") or "",
            "format": t.get("format") or "nam", "image": images[0] if images else "",
            "createur": u.get("display_name") or u.get("username") or "", "pseudo": u.get("username") or "",
            "avatar": u.get("avatar_url") or "", "verifie": bool(u.get("is_verified")),
            "a2": t.get("a2_models_count", -1) if t.get("a2_models_count") is not None else -1,
            "makes": [m.get("name", "") for m in (t.get("makes") or []) if isinstance(m, dict)][:6],
            "tags": [m.get("name", "") for m in (t.get("tags") or []) if isinstance(m, dict)][:8],
            "telechargements": t.get("downloads_count") or 0, "url": t.get("url") or ""}
