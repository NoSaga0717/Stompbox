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
      reglages: {CARD: "M4", RATE: 48000, PERIOD: s.buffer, NPERIODS: 2, INPUT_DBU: s.cal},
      volume: s.volume, buffers: [64, 128, 256, 512],
      midi: {canal: s.midi.canal, assignations: assignations()}}),
    "/api/config": () => copie(CFG),
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
