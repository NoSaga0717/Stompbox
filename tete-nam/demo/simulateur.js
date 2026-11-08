/* Tête NAM simulée, pour essayer l'interface (ui.html) sans la tête.
 *
 * ui.html envoie tous ses appels /api/... à window.teteTransport quand il
 * existe (c'est ce que fait l'appli Android en Bluetooth). Ici, ce
 * transport répond à la place de web.py, avec une chaîne, des presets et
 * des mesures simulés. Les réglages de chaque type de bloc viennent du vrai
 * moteur (config-demo.js, capturé par fabriquer.py). Pas de son.
 * Les presets sont gardés dans le navigateur (localStorage).
 */
(function () {
  "use strict";
  window.teteDemo = true;
  const CFG = window.TETE_DEMO_CONFIG;
  const CLE = "tete-nam-demo-v1";

  const copie = o => JSON.parse(JSON.stringify(o));
  const typeDe = id => id.split("_")[0];

  function defauts(type) {
    const v = {};
    for (const p of (CFG.types[type] || {}).params || []) {
      v[p.nom] = p.type === "liste" ? p.valeurs[Math.max(0, Math.round(p.defaut))] || p.valeurs[0]
        : p.type === "fichier" ? (CFG.modeles[0] || "") : p.defaut;
    }
    return v;
  }
  function bloc(id, actif, valeurs) {
    return {id, type: typeDe(id), actif, valeurs: Object.assign(defauts(typeDe(id)), valeurs || {})};
  }

  const PRESETS_DEPART = {
    "Clean délai": [
      bloc("Compressor", true, {Thresh: -24, Ratio: 3}),
      bloc("NAM", true, {Model: CFG.modeles[1] || CFG.modeles[0]}),
      bloc("Chorus", true),
      bloc("Delay", true, {Delay: 380, Mix: 0.35, FBack: 0.35}),
      bloc("Reverb", true, {Blend: 25}),
    ],
    "Crunch": [
      bloc("NoiseGate", true),
      bloc("Screamer", true, {Drive: 0.3, Level: -2}),
      bloc("NAM", true, {Model: CFG.modeles[0], OutputMode: "Calibrated"}),
      bloc("NAM_2", true, {Model: CFG.modeles[2] || CFG.modeles[0]}),
      bloc("Reverb", false),
    ],
    "Lead": [
      bloc("NoiseGate", true),
      bloc("Boost", true),
      bloc("NAM", true, {Model: CFG.modeles[2] || CFG.modeles[0]}),
      bloc("Delay", true, {Delay: 450, Mix: 0.3}),
      bloc("Reverb", true, {Blend: 30}),
    ],
  };

  // --- État (gardé dans le navigateur) -------------------------------------
  let s = null;
  try { s = JSON.parse(localStorage.getItem(CLE)); } catch (e) { s = null; }
  if (!s || !s.presets) {
    s = {presets: copie(PRESETS_DEPART), preset: "Clean délai", chaine: copie(PRESETS_DEPART["Clean délai"]),
         modifie: false, cal: 16, volume: 0, buffer: 128};
  }
  if (!s.midi) s.midi = {canal: 0, global: {}, presets: {}, courant: {}};
  let rev = 1, accordeur = false, vus = new Set(s.chaine.map(b => b.id));
  const histo = [];
  // Son simulé pour la veille de l'écran : des accords grattés qui
  // s'éteignent doucement, avec un peu de jeu en notes entre deux
  let attaque = 0, force = 0, couleur = 0;
  function niveauSimule() {
    const t = performance.now() / 1000;
    if (t - attaque > 1.1 + Math.random() * 1.6) {
      attaque = t; force = 0.35 + Math.random() * 0.6;
      couleur = Math.random() * 2 - 1;      // accord plus sourd (-1) ou plus brillant (+1)
    }
    const e = force * Math.exp(-(t - attaque) * 1.6) * (0.9 + 0.1 * Math.sin(t * 40));
    // Spectre d'une guitare saturée : bosse vers 300-1500 Hz, aigus qui
    // s'éteignent plus vite que les graves après l'attaque
    const age = t - attaque;
    // 24 tiers d'octave : bosse vers 300-1500 Hz, quelques harmoniques
    // marquées, aigus qui s'éteignent plus vite que les graves
    const bandes = [];
    for (let i = 0; i < 24; i++) {
      const oct = i / 3, forme = Math.exp(-Math.pow((oct - 3.3 - 0.6 * couleur) / 2.1, 2));
      const harmo = 0.65 + 0.35 * Math.sin(i * 1.9 + attaque * 3) * Math.sin(t * (1.5 + i * 0.2));
      bandes.push(e * 0.4 * forme * harmo * Math.exp(-age * oct * 0.3) / Math.pow(10, i / 20));
    }
    return {ok: true, rms: e * 0.5, grave: e * (0.25 + 0.15 * Math.sin(t * 0.7)), aigu: e * 0.4, crete: Math.min(1, e * 0.9), bandes};
  }
  // Wi-Fi simulé : quelques réseaux, « Studio » est enregistré
  const wifi = {ssid: "", hotspot: true, connus: ["Studio"], resultat: null,
    reseaux: [["Studio", 78, true], ["Box-Maison", 64, true], ["Voisin 5G", 41, true], ["Café gratuit", 30, false]]};
  function wifiSimule() {
    return {ok: true, ssid: wifi.ssid, ip: wifi.ssid ? "192.168.1.42" : "192.168.77.1", hotspot: !wifi.ssid && wifi.hotspot,
      cable: "", hotspot_nom: "TeteNAM", resultat: wifi.resultat,
      reseaux: wifi.reseaux.map(([n, sig, sec]) => ({ssid: n, signal: sig, securise: sec, actif: n === wifi.ssid, connu: wifi.connus.includes(n)}))};
  }
  function actionWifi(d) {
    if (d.action === "connecter") {
      const n = wifi.reseaux.find(r => r[0] === d.ssid);
      const ok = n && (!n[2] || wifi.connus.includes(d.ssid) || (d.mdp || "").length >= 8);
      wifi.resultat = {etat: "en_cours", ssid: d.ssid, message: `Connexion à « ${d.ssid} »…`, t: Date.now() / 1000};
      setTimeout(() => {
        if (ok) { wifi.ssid = d.ssid; if (!wifi.connus.includes(d.ssid)) wifi.connus.push(d.ssid); }
        wifi.resultat = {etat: ok ? "ok" : "echec", ssid: d.ssid, t: Date.now() / 1000, message: ok ?
          `Connecté à « ${d.ssid} » (192.168.1.42). Page : http://tetenam.local:8080` :
          `Connexion à « ${d.ssid} » impossible : mot de passe refusé ou réseau trop loin.`};
      }, 2500);
    } else if (d.action === "oublier") {
      wifi.connus = wifi.connus.filter(n => n !== d.ssid); if (wifi.ssid === d.ssid) wifi.ssid = "";
    } else if (d.action === "hotspot") { wifi.ssid = ""; wifi.hotspot = true; }
    return {ok: true};
  }
  function sauver() { try { localStorage.setItem(CLE, JSON.stringify(s)); } catch (e) { /* pas grave */ } }
  function change() { s.modifie = true; rev++; sauver(); }
  const noms = () => Object.keys(s.presets).sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
  const numero = () => { const i = noms().indexOf(s.preset); return i >= 0 ? i + 1 : null; };
  const trouver = id => { const b = s.chaine.find(x => x.id === id); if (!b) throw new Error("Bloc inconnu : " + id); return b; };

  // Charge DSP simulée : un NAM A2 complet ≈ 30 %, un effet ≈ 0,5 %
  function dsp() {
    let d = 4;
    for (const b of s.chaine) {
      if (!b.actif) continue;
      d += b.type === "NAM" ? 8 + 22 * (+b.valeurs.Quality || 0) : 0.5;
    }
    d *= 128 / s.buffer > 1 ? 1.08 : 1;
    return Math.max(1, d + (Math.random() - 0.5) * 3);
  }
  setInterval(() => { histo.push(dsp()); if (histo.length > 120) histo.shift(); }, 1000);

  const NOTES = ["Do", "Do#", "Ré", "Ré#", "Mi", "Fa", "Fa#", "Sol", "Sol#", "La", "La#", "Si"];
  function tuner() {
    if (!accordeur) return null;
    const t = Date.now() / 1000;
    const hz = 110 * Math.pow(2, (Math.sin(t / 2.5) * 18) / 1200);   // un La qui oscille autour du juste
    const demi = 12 * Math.log2(hz / 440) + 69, proche = Math.round(demi);
    return {actif: true, hz: Math.round(hz * 10) / 10, note: NOTES[proche % 12], octave: Math.floor(proche / 12) - 1,
            cents: Math.round((demi - proche) * 100)};
  }

  // --- Réponses de l'API (mêmes champs que web.py) -----------------------------
  const lectures = {
    "/api/etat": () => ({rev, cfg: CFG.cfg, moteur: true, dsp: histo.length ? histo[histo.length - 1] : dsp(), xruns: 0,
      temp: 52 + Math.random() * 2, throttled: "0x0", preset: s.preset, numero: numero(), modifie: s.modifie,
      chargement: false, accordeur: tuner(), midi: etatMidi()}),
    "/api/tout": () => ({rev, preset: s.preset, numero: numero(), modifie: s.modifie, presets: noms(), chaine: copie(s.chaine),
      reglages: {CARD: "M4", RATE: 48000, PERIOD: s.buffer, NPERIODS: 2, INPUT_DBU: s.cal, VEILLE: s.veille ?? 7, VEILLE_STYLE: s.veilleStyle || "onde"},
      volume: s.volume, buffers: [64, 128, 256, 512],
      midi: {canal: s.midi.canal, assignations: assignations()}}),
    "/api/config": () => copie(CFG),
    "/api/niveau": () => niveauSimule(),
    "/api/wifi": () => wifiSimule(),
    "/api/systeme": () => ({histo: histo.slice(), temp: 53, throttled: "0x0", xruns: 0, dsp: histo[histo.length - 1],
      version: "démo", service: "active"}),
  };

  function charger(nom) {
    if (!s.presets[nom]) throw new Error("Preset introuvable : " + nom);
    s.chaine = copie(s.presets[nom]); s.preset = nom; s.modifie = false;
    s.midi.courant = copie(s.midi.presets[nom] || {});
    s.chaine.forEach(b => vus.add(b.id)); rev++; sauver();
  }

  const ecritures = {
    "/api/param": d => {
      const b = trouver(d.id), p = (CFG.types[b.type].params || []).find(q => q.nom === d.nom);
      if (!p) throw new Error("Réglage inconnu : " + d.nom);
      b.valeurs[d.nom] = (p.type === "liste" || p.type === "fichier") ? String(d.valeur)
        : Math.min(p.max, Math.max(p.min, +d.valeur));
      change(); return {ok: true};
    },
    "/api/actif": d => { trouver(d.id).actif = !!d.actif; change(); return {ok: true}; },
    "/api/ordre": d => {
      const ordre = d.ordre.map(String);
      if (ordre.slice().sort().join() !== s.chaine.map(b => b.id).sort().join()) throw new Error("Ordre invalide");
      s.chaine = ordre.map(trouver); change(); return {ok: true};
    },
    "/api/ajouter": d => {
      if (!CFG.types[d.type]) throw new Error("Type de bloc inconnu : " + d.type);
      if (s.chaine.length >= 16) throw new Error("16 blocs au maximum");
      let n = 1, id = d.type;
      while (s.chaine.some(b => b.id === id) || vus.has(id)) { n++; id = d.type + "_" + n; }
      vus.add(id);
      const i = s.chaine.findIndex(b => b.id === d.apres);
      s.chaine.splice(i >= 0 ? i + 1 : s.chaine.length, 0, bloc(id, true));
      change(); return {ok: true, id};
    },
    "/api/retirer": d => {
      trouver(d.id); s.chaine = s.chaine.filter(b => b.id !== d.id);
      for (const k of Object.keys(s.midi.courant)) if (s.midi.courant[k].id === d.id) delete s.midi.courant[k];
      change(); return {ok: true};
    },
    "/api/midi": d => apiMidi(d),
    "/api/preset": d => {
      const liste = noms();
      if (d.action === "charger") charger(String(d.nom));
      else if (d.action === "suivant" || d.action === "precedent") {
        if (!liste.length) throw new Error("Aucun preset enregistré");
        let k = liste.indexOf(s.preset);
        k = k < 0 ? (d.action === "suivant" ? 0 : -1) : k + (d.action === "suivant" ? 1 : -1);
        charger(liste[((k % liste.length) + liste.length) % liste.length]);
      } else if (d.action === "sauver") {
        const nom = String(d.nom || s.preset || "").replace(/\s+/g, " ").trim().slice(0, 40);
        if (!nom) throw new Error("Donnez un nom au preset");
        s.presets[nom] = copie(s.chaine); s.preset = nom; s.modifie = false;
        s.midi.presets[nom] = copie(s.midi.courant); rev++; sauver();
      } else if (d.action === "supprimer") {
        delete s.presets[d.nom]; delete s.midi.presets[d.nom];
        if (s.preset === d.nom) { s.preset = ""; s.modifie = true; }
        rev++; sauver();
      } else if (d.action === "nouveau") { s.chaine = []; s.preset = ""; s.midi.courant = {}; change(); }
      else throw new Error("Action inconnue");
      return {ok: true, preset: s.preset};
    },
    "/api/calibration": d => { s.cal = Math.round(+d.valeur * 10) / 10; rev++; sauver(); return {ok: true}; },
    "/api/volume": d => { s.volume = Math.min(40, Math.max(-40, +d.valeur)); rev++; sauver(); return {ok: true}; },
    "/api/buffer": d => { s.buffer = +d.taille; rev++; sauver(); return {ok: true}; },
    "/api/accordeur": d => { accordeur = !!d.actif; return {ok: true}; },
    "/api/wifi": d => actionWifi(d),
    "/api/veille": d => {
      if (d.secondes != null) s.veille = Math.max(0, Math.min(600, +d.secondes));
      if (d.style) s.veilleStyle = d.style === "spectre" ? "spectre" : "onde";
      rev++; sauver(); return {ok: true};
    },
  };

  // --- MIDI (même logique que web.py) -------------------------------------------
  const GLOBALES = {suivant: "Preset suivant", precedent: "Preset précédent", accordeur: "Accordeur"};
  const vivant = {dernier: "", t: 0, attente: null, appris: ""};
  const meme = (a, b) => ["cible", "id", "nom"].every(k => (a[k] || "") === (b[k] || ""));
  const paramDe = (id, nom) => ((CFG.types[typeDe(id)] || {}).params || []).find(q => q.nom === nom);
  function libelle(c) {
    if (GLOBALES[c.cible]) return GLOBALES[c.cible];
    const t = CFG.types[typeDe(c.id)], num = c.id.split("_")[1];
    const nom = (t ? t.court : c.id) + (num ? " " + num : "");
    if (c.cible === "actif") return nom + " : allumé / éteint";
    const p = paramDe(c.id, c.nom);
    return nom + " : " + (p ? p.libelle : c.nom);
  }
  function assignations() {
    const r = [];
    for (const [portee, table] of [["preset", s.midi.courant], ["global", s.midi.global]])
      for (const cle of Object.keys(table).sort((a, b) => +a.split(":")[1] - +b.split(":")[1]))
        r.push(Object.assign({}, table[cle], {cle, cc: +cle.split(":")[1], libelle: libelle(table[cle]), portee}));
    return r;
  }
  function etatMidi() {
    if (vivant.attente && Date.now() - vivant.attente.t > 30000) vivant.attente = null;
    return {dernier: vivant.dernier, age: vivant.t ? (Date.now() - vivant.t) / 1000 : null,
            attente: !!vivant.attente, appris: vivant.appris, ports: ["Pédalier virtuel"]};
  }
  function apprendre(cc) {
    let cible = vivant.attente.cible; vivant.attente = null;
    const cle = "cc:" + cc, globale = !!GLOBALES[cible.cible];
    const table = globale ? "global" : "courant", autre = globale ? "courant" : "global";
    for (const k of Object.keys(s.midi[table])) if (meme(s.midi[table][k], cible)) delete s.midi[table][k];
    delete s.midi[autre][cle];
    if (cible.cible === "actif") cible = Object.assign({}, cible, {mode: "bascule"});
    s.midi[table][cle] = cible;
    vivant.appris = "CC " + cc + " → " + libelle(cible);
    if (globale) { rev++; sauver(); } else change();
  }
  function executerCC(cc, v) {
    const c = s.midi.courant["cc:" + cc] || s.midi.global["cc:" + cc];
    if (!c) return;
    if (c.cible === "actif") {
      const b = s.chaine.find(x => x.id === c.id); if (!b) return;
      if (c.mode === "valeur") { if (b.actif === (v >= 64)) return; b.actif = v >= 64; }
      else if (v === 0) return;
      else b.actif = !b.actif;
      change();
    } else if (c.cible === "param") {
      const b = s.chaine.find(x => x.id === c.id), p = paramDe(c.id, c.nom); if (!b || !p) return;
      let x = p.min + (p.max - p.min) * v / 127; if (p.type === "entier") x = Math.round(x);
      b.valeurs[c.nom] = x; change();
    } else if (v === 0) return;
    else if (c.cible === "suivant" || c.cible === "precedent") ecritures["/api/preset"]({action: c.cible});
    else if (c.cible === "accordeur") { accordeur = !accordeur; rev++; }
  }
  function decrire(statut, d1, d2) {
    const t = statut & 0xF0, canal = (statut & 0x0F) + 1;
    return t === 0xB0 ? `Canal ${canal} · CC ${d1} = ${d2}` : t === 0xC0 ? `Canal ${canal} · Program Change ${d1}` : `Canal ${canal} · message`;
  }
  /* Appelé par le pédalier virtuel de la page de démo */
  window.teteDemoMidi = function (statut, d1, d2) {
    vivant.dernier = "Pédalier virtuel · " + decrire(statut, d1, d2); vivant.t = Date.now();
    const canal = (statut & 0x0F) + 1;
    if (s.midi.canal && canal !== s.midi.canal) return;
    if ((statut & 0xF0) === 0xC0) { const l = noms(); if (d1 < l.length) charger(l[d1]); }
    else if ((statut & 0xF0) === 0xB0) {
      if (vivant.attente) { apprendre(d1); return; }
      executerCC(d1, d2);
    }
  };
  function apiMidi(d) {
    if (d.action === "apprendre") {
      const c = d.cible || {}, cible = {cible: String(c.cible || "")};
      if (cible.cible === "actif" || cible.cible === "param") {
        cible.id = String(c.id || ""); trouver(cible.id);
        if (cible.cible === "param") cible.nom = String(c.nom || "");
      } else if (!GLOBALES[cible.cible]) throw new Error("Cible MIDI inconnue");
      vivant.attente = {cible, t: Date.now()}; vivant.appris = "";
    } else if (d.action === "annuler") vivant.attente = null;
    else if (d.action === "oublier") {
      if (s.midi.courant[d.cle]) { delete s.midi.courant[d.cle]; change(); }
      else { delete s.midi.global[d.cle]; rev++; sauver(); }
    } else if (d.action === "mode") {
      const c = s.midi.courant[d.cle]; if (!c) throw new Error("Assignation inconnue");
      c.mode = d.mode; change();
    } else if (d.action === "canal") { s.midi.canal = Math.max(0, Math.min(16, +d.canal)); rev++; sauver(); }
    else throw new Error("Action inconnue");
    return {ok: true};
  }

  // --- TONE3000, bibliothèque et cartes son simulés (catalogue fictif) ------------
  const GEARS_DEMO = ["amp", "amp-cab", "pedal"];
  const TONES_DEMO = ["Ampli britannique 1987", "Ampli américain clean", "Lead californien", "Combo vintage 1965",
    "Overdrive vert", "Haut-gain moderne", "Boutique dumble-style", "Fuzz des années 70", "Tête britannique 800",
    "Crunch texan", "Clean jazz", "Ampli à transistors 1982"].map((titre, i) => ({
      id: 101 + i, titre, gear: GEARS_DEMO[i % 3], format: "nam", image: "", createur: ["Studio Démo", "Capteur Fou", "NAM Lab"][i % 3],
      pseudo: "demo" + (i % 3), avatar: "", verifie: i % 3 === 0, a2: 2 + (i % 3), makes: [["Britannique", "Américain", "Boutique"][i % 3]],
      tags: [["crunch", "clean", "lead"][i % 3]], telechargements: 1000 - i * 50, url: "https://www.tone3000.com",
      description: "Tone fictif de la démo : sur la vraie tête, la fiche vient de TONE3000.", licence: "t3k"}));
  const modelesDemo = t => ["Clean", "Crunch", "Lead", "Gain max"].slice(0, t.a2).map((nom, k) => ({id: t.id * 10 + k, nom, taille: "standard"}));
  s.bib = s.bib || {etiquettes: ["clean", "edge", "crunch", "lead"], fiches: {}};
  s.t3kFichiers = s.t3kFichiers || [];
  s.t3k = s.t3k || {connecte: false};
  const nam = () => CFG.types.NAM.params.find(p => p.type === "fichier");
  const ajouterModele = f => {
    if (!CFG.modeles.includes(f)) CFG.modeles.push(f);
    const p = nam(); if (p && !p.valeurs.includes(f)) p.valeurs.push(f);
  };
  s.t3kFichiers.forEach(ajouterModele);
  let t3kRev = 0, bibRev = 0, connexion = null, tache = null, essai = null;
  const fichierDe = mid => Object.keys(s.bib.fiches).find(f => s.bib.fiches[f].t3k && s.bib.fiches[f].t3k.model_id === mid && CFG.modeles.includes(f)) || null;
  const fiche = f => (s.bib.fiches[f] = s.bib.fiches[f] || {etiquettes: [], favori: false, nom: "", source: "fichier"});
  function garderModele(t, m, etiquettes) {
    const deja = fichierDe(m.id); if (deja) return deja;
    const f = (t.titre + "-" + m.nom).normalize("NFD").replace(/[^\w-]+/g, "_");
    ajouterModele(f); s.t3kFichiers.push(f);
    Object.assign(fiche(f), {source: "tone3000", t3k: {tone_id: t.id, model_id: m.id, tone: t.titre, modele: m.nom, createur: t.createur,
      gear: t.gear, makes: t.makes, tags: t.tags, image: "", taille: m.taille, licence: "t3k", url: t.url}});
    etiquettes.forEach(e => { if (!fiche(f).etiquettes.includes(e)) fiche(f).etiquettes.push(e); });
    bibRev++; sauver(); return f;
  }
  const nomT3k = t => t.modele ? `${t.tone} · ${t.modele}` : t.tone;
  lectures["/api/bibliotheque"] = () => ({ok: true, etiquettes: s.bib.etiquettes, rev: bibRev,
    essai: essai && {bloc: essai.bloc, fichier: essai.fichier, nom: essai.nom, tone: essai.tone},
    modeles: CFG.modeles.filter(f => !f.startsWith("_essai-")).map(f => {
      const e = s.bib.fiches[f] || {}, t = e.t3k || null;
      return {fichier: f, nom: e.nom || (t ? nomT3k(t) : f), renomme: !!e.nom, etiquettes: e.etiquettes || [],
              favori: !!e.favori, source: e.source || "fichier", date: "", t3k: t};
    })});
  ecritures["/api/bibliotheque"] = d => {
    if (d.action === "nouvelle_etiquette") { if (!s.bib.etiquettes.includes(d.nom)) s.bib.etiquettes.push(d.nom); }
    else if (d.action === "retirer_etiquette") s.bib.etiquettes = s.bib.etiquettes.filter(e => e !== d.nom);
    else if (d.action === "etiqueter") {
      const l = [...new Set(d.etiquettes.map(e => String(e).trim().toLowerCase()).filter(Boolean))];
      l.forEach(e => { if (!s.bib.etiquettes.includes(e)) s.bib.etiquettes.push(e); });
      fiche(d.fichier).etiquettes = l;
    } else if (d.action === "favori") fiche(d.fichier).favori = !!d.favori;
    else if (d.action === "renommer") fiche(d.fichier).nom = String(d.nom || "").trim();
    else if (d.action === "utilisation") return {ok: true, chaine: s.chaine.filter(b => b.valeurs.Model === d.fichier).map(b => b.id),
      presets: noms().filter(n => s.presets[n].some(b => b.valeurs && b.valeurs.Model === d.fichier))};
    else if (d.action === "supprimer") {
      if (s.chaine.some(b => b.valeurs.Model === d.fichier)) throw new Error("Ce modèle est dans la chaîne en cours : changez-le d'abord");
      CFG.modeles.splice(CFG.modeles.indexOf(d.fichier), 1); const p = nam(); if (p) p.valeurs = p.valeurs.filter(x => x !== d.fichier);
      s.t3kFichiers = s.t3kFichiers.filter(x => x !== d.fichier); delete s.bib.fiches[d.fichier]; CFG.cfg += "x";
    } else throw new Error("Action inconnue");
    bibRev++; sauver(); return {ok: true};
  };
  lectures["/api/tone3000"] = () => {
    if (connexion && connexion.etat === "attente" && Date.now() > connexion.t + 6000) {   // la démo valide toute seule
      connexion.etat = "ok"; s.t3k = {connecte: true}; t3kRev++; sauver();
    }
    return {ok: true, cle: true, recherche: s.t3kRecherche !== false, connecte: !!s.t3k.connecte,
      utilisateur: s.t3k.connecte ? {username: "demo", nom: "Compte de démo", avatar: ""} : null,
      connexion: connexion && {etat: connexion.etat, code: "DEMO-2026", url: "https://www.tone3000.com/activate",
        url_complete: "https://www.tone3000.com/activate", message: "", reste: Math.max(0, 600 - Math.round((Date.now() - connexion.t) / 1000))},
      tache: tache && Object.assign({}, tache), essai: essai && {bloc: essai.bloc, fichier: essai.fichier, nom: essai.nom, tone: essai.tone},
      choix: null, rev: t3kRev};
  };
  ecritures["/api/tone3000"] = d => {
    const tone = id => { const t = TONES_DEMO.find(x => x.id === +id); if (!t) throw new Error("Tone introuvable"); return t; };
    switch (d.action) {
      case "connecter": connexion = {etat: "attente", t: Date.now()}; break;
      case "annuler": connexion = null; break;
      case "deconnecter": s.t3k = {connecte: false}; connexion = null; sauver(); break;
      case "recherche": s.t3kRecherche = !!d.actif; sauver(); break;
      case "cle": break;
      case "liste": {
        const mots = String(d.texte || "").toLowerCase().split(/\s+/).filter(Boolean);
        let l = TONES_DEMO.filter(t => (!d.gear || t.gear === d.gear) && mots.every(m => (t.titre + " " + t.createur + " " + t.tags).toLowerCase().includes(m)));
        if (d.quoi === "favorited") l = l.filter((t, i) => i % 4 === 0);
        if (d.quoi === "downloaded") l = l.filter(t => modelesDemo(t).some(m => fichierDe(m.id)));
        if (d.quoi === "created") l = [];
        if (d.quoi === "latest") l = l.slice().reverse();
        return {ok: true, tones: l.map(t => Object.assign({}, t, {chez_moi: modelesDemo(t).filter(m => fichierDe(m.id)).length})),
                page: 1, pages: 1, total: l.length, masques: 0};
      }
      case "tone": { const t = tone(d.id); return {ok: true, tone: t, modeles: modelesDemo(t).map(m => Object.assign({fichier: fichierDe(m.id)}, m))}; }
      case "telecharger": {
        const t = tone(d.tone_id), choisis = modelesDemo(t).filter(m => d.modeles.includes(m.id));
        if (!choisis.length) throw new Error("Choisissez au moins un modèle");
        tache = {tone_id: t.id, titre: t.titre, total: choisis.length, fait: 0, deja: 0, en_cours: choisis[0].nom, erreurs: [], fichiers: [], fini: false};
        const pas = () => {
          const m = choisis[tache.fait]; tache.fichiers.push(garderModele(t, m, d.etiquettes || [])); tache.fait++;
          tache.en_cours = choisis[tache.fait] ? choisis[tache.fait].nom : ""; tache.fini = tache.fait >= tache.total;
          CFG.cfg += "x"; t3kRev++; if (!tache.fini) setTimeout(pas, 700);
        };
        setTimeout(pas, 700); break;
      }
      case "stop": break;
      case "essayer": {
        const t = tone(d.tone_id), m = modelesDemo(t).find(x => x.id === +d.model_id);
        let b = s.chaine.find(x => x.id === d.bloc && x.type === "NAM") || s.chaine.find(x => x.type === "NAM");
        if (!b) { ecritures["/api/ajouter"]({type: "NAM", apres: s.chaine.length ? s.chaine[s.chaine.length - 1].id : null}); b = s.chaine.find(x => x.type === "NAM"); }
        const deja = fichierDe(m.id);
        if (deja) { b.valeurs.Model = deja; change(); return {ok: true, bloc: b.id, deja}; }
        if (essai) ecritures["/api/tone3000"]({action: "abandonner"});
        const f = "_essai-" + m.id; ajouterModele(f);
        essai = {bloc: b.id, ancien: b.valeurs.Model, fichier: f, nom: m.nom, tone: t.titre, t, m};
        b.valeurs.Model = f; CFG.cfg += "x"; change(); t3kRev++; return {ok: true, bloc: b.id};
      }
      case "garder": {
        if (!essai) throw new Error("Aucun essai en cours");
        const f = garderModele(essai.t, essai.m, d.etiquettes || []);
        s.chaine.forEach(b => { if (b.valeurs.Model === essai.fichier) b.valeurs.Model = f; });
        essai = null; CFG.cfg += "x"; change(); t3kRev++; return {ok: true, fichier: f};
      }
      case "abandonner": {
        if (essai) s.chaine.forEach(b => { if (b.valeurs.Model === essai.fichier) b.valeurs.Model = essai.ancien; });
        essai = null; change(); break;
      }
      case "catalogue": return {ok: true, url: "https://www.tone3000.com"};
      case "vu": break;
      default: throw new Error("Action inconnue");
    }
    t3kRev++; return {ok: true};
  };
  lectures["/api/cartes"] = () => ({ok: true, courante: s.carte || "M4", cartes: [{id: "M4", nom: "MOTU M4"},
    {id: "UMC1820", nom: "BEHRINGER UMC1820"}, {id: "aucune", nom: "Aucune (essai sans son)"}]});
  ecritures["/api/carte"] = d => {
    s.cartesCal = s.cartesCal || {M4: 16}; s.cartesCal[s.carte || "M4"] = s.cal;
    s.carte = d.id; const connue = d.id in s.cartesCal; if (connue) s.cal = s.cartesCal[d.id];
    rev++; sauver(); return {ok: true, calibration: s.cal, connue};
  };
  const etatAvant = lectures["/api/etat"];
  lectures["/api/etat"] = () => Object.assign(etatAvant(), {bib: bibRev, t3k: t3kRev});
  const toutAvant = lectures["/api/tout"];
  lectures["/api/tout"] = () => { const t = toutAvant(); t.reglages.CARD = s.carte || "M4"; return t; };
  const ajoutAvant = ecritures["/api/ajouter"];
  ecritures["/api/ajouter"] = d => {
    const r = ajoutAvant(d);
    if (d.modele) { s.chaine.find(b => b.id === r.id).valeurs.Model = String(d.modele); change(); }
    return r;
  };

  // Petit délai, comme une vraie tête en Wi-Fi
  const attendre = ms => new Promise(r => setTimeout(r, ms));
  window.teteTransport = async function (chemin, corps) {
    await attendre(15 + Math.random() * 25);
    try {
      if (corps == null) {
        if (!lectures[chemin]) throw new Error("Introuvable : " + chemin);
        return lectures[chemin]();
      }
      if (!ecritures[chemin]) throw new Error("Introuvable : " + chemin);
      return ecritures[chemin](corps);
    } catch (e) {
      return {ok: false, erreur: e.message};
    }
  };
  window.teteEnvoyerFichiers = async function () {
    return [{ok: false, erreur: "Démo : pas d'envoi de modèles (il faut la vraie tête)"}];
  };
  window.teteDemoRemettre = function () { try { localStorage.removeItem(CLE); } catch (e) { /* rien */ } location.reload(); };
})();
