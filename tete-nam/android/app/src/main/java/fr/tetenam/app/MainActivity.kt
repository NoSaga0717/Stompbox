package fr.tetenam.app

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothManager
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.webkit.JavascriptInterface
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * L'appli affiche la même interface que l'écran de la tête (ui.html),
 * demandée à la tête en Bluetooth et gardée en cache. Les appels de la page
 * vers l'API de la tête passent par window.teteTransport(chemin, corps),
 * défini ici et transmis en Bluetooth (classe Tete).
 */
class MainActivity : ComponentActivity() {
    private lateinit var tete: Tete
    private lateinit var web: WebView
    private lateinit var voile: LinearLayout       // écran de connexion (avant la page)
    private lateinit var etatTexte: TextView
    private lateinit var boutonAutoriser: Button
    private lateinit var bandeau: TextView         // connexion perdue, par-dessus la page

    private var pageAffichee: String? = null       // signature de la page affichée
    private var choixEnCours = false               // pendant le choix des fichiers, on garde la connexion
    private var idChoix = -1                       // requête JS qui attend le choix des fichiers

    private val cachePage by lazy { File(filesDir, "ui.html") }
    private val cacheSig by lazy { File(filesDir, "ui.sig") }

    private val permissions: Array<String> =
        if (Build.VERSION.SDK_INT >= 31)
            arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
        else
            arrayOf(Manifest.permission.ACCESS_FINE_LOCATION)

    private val demande = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        verifier()
    }

    private val choixFichiers = registerForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        choixEnCours = false
        val id = idChoix
        idChoix = -1
        tete.envoyerFichiers(uris,
            progression = { t -> js("window.teteProgression && window.teteProgression(${JSONObject.quote(t)})") },
            fin = { res -> repondre(id, res.toString(), false) })
    }

    // ------------------------------------------------------------ interface

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        tete = Tete(applicationContext)

        val fond = Color.parseColor("#0B0A09")
        val racine = FrameLayout(this).apply { setBackgroundColor(fond) }

        web = WebView(this).apply {
            setBackgroundColor(fond)
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            settings.textZoom = 100
            addJavascriptInterface(Pont(), "TeteAndroid")
            // La page ne doit jamais naviguer ailleurs
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(v: WebView, r: WebResourceRequest) = true
            }
            visibility = View.INVISIBLE
        }
        racine.addView(web, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))

        voile = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setBackgroundColor(fond)
            setPadding(48, 48, 48, 48)
        }
        voile.addView(TextView(this).apply {
            text = "Tête NAM"
            textSize = 28f
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(Color.parseColor("#D6AC5A"))
            gravity = Gravity.CENTER
        })
        etatTexte = TextView(this).apply {
            textSize = 17f
            setTextColor(Color.parseColor("#ECE6DA"))
            gravity = Gravity.CENTER
            setPadding(0, 32, 0, 32)
        }
        voile.addView(etatTexte)
        boutonAutoriser = Button(this).apply {
            text = "Autoriser le Bluetooth"
            visibility = View.GONE
            setOnClickListener { demande.launch(permissions) }
        }
        voile.addView(boutonAutoriser)
        racine.addView(voile, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))

        bandeau = TextView(this).apply {
            textSize = 14f
            setTextColor(Color.BLACK)
            setBackgroundColor(Color.parseColor("#D6AC5A"))
            gravity = Gravity.CENTER
            setPadding(16, 12, 16, 12)
            visibility = View.GONE
        }
        racine.addView(bandeau, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM))

        // Android 15 dessine sous les barres système : on garde la page en dehors
        ViewCompat.setOnApplyWindowInsetsListener(racine) { v, insets ->
            val b = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.ime())
            v.setPadding(b.left, b.top, b.right, b.bottom)
            WindowInsetsCompat.CONSUMED
        }
        setContentView(racine)

        tete.surConnexion = { texte, connecte -> connexionChangee(texte, connecte) }
        etatTexte.text = "Recherche de la tête…"

        // Retour : ferme d'abord une fenêtre ouverte dans la page
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (web.visibility != View.VISIBLE) {
                    finish(); return
                }
                web.evaluateJavascript(
                    "(function(){var f=document.getElementById('feuille');" +
                        "if(f&&f.classList.contains('ouverte')){document.getElementById('ffermer').click();return 1}" +
                        "var a=document.getElementById('accord');" +
                        "if(a&&a.classList.contains('ouvert')){document.getElementById('afermer').click();return 1}" +
                        "return 0})()"
                ) { r -> if (r != "1") finish() }
            }
        })
    }

    private fun connexionChangee(texte: String, connecte: Boolean) {
        if (connecte) {
            bandeau.visibility = View.GONE
            etatTexte.text = "$texte\nChargement de l'interface…"
            chargerPage()
        } else if (web.visibility == View.VISIBLE) {
            bandeau.text = texte
            bandeau.visibility = View.VISIBLE
        } else {
            etatTexte.text = texte
        }
    }

    // ------------------------------------------------------- page de la tête

    private fun lireCache(): Pair<String, String>? = try {
        if (cachePage.exists() && cacheSig.exists()) Pair(cachePage.readText(), cacheSig.readText()) else null
    } catch (_: Exception) {
        null
    }

    private fun chargerPage() {
        val cache = lireCache()
        tete.page(cache?.second) { r ->
            when {
                r.optBoolean("inchange") && cache != null -> afficher(cache.first, cache.second)
                r.has("html") -> {
                    val html = r.getString("html")
                    val sig = r.optString("sig")
                    try {
                        cachePage.writeText(html)
                        cacheSig.writeText(sig)
                    } catch (_: Exception) {
                    }
                    afficher(html, sig)
                }
                cache != null -> afficher(cache.first, cache.second)   // tête muette : version en cache
                else -> etatTexte.text = "Interface non reçue (${r.optString("erreur")}), nouvel essai…"
                    .also { web.postDelayed({ if (tete.connecte && pageAffichee == null) chargerPage() }, 3000) }
            }
        }
    }

    private fun afficher(html: String, sig: String) {
        voile.visibility = View.GONE
        web.visibility = View.VISIBLE
        if (pageAffichee == sig) return          // même page déjà affichée : elle se resynchronise seule
        pageAffichee = sig
        // Le pont est défini avant le script de la page
        val i = html.indexOf("<head>", ignoreCase = true)
        val avecPont = if (i >= 0) html.substring(0, i + 6) + PONT_JS + html.substring(i + 6) else PONT_JS + html
        web.loadDataWithBaseURL("https://tete-nam.local/", avecPont, "text/html", "utf-8", null)
    }

    private fun js(code: String) = web.post { web.evaluateJavascript(code, null) }

    private fun repondre(id: Int, json: String, transport: Boolean) {
        if (id < 0) return
        js("window.__teteReponse && window.__teteReponse($id, ${JSONObject.quote(json)}, $transport)")
    }

    /** Appelé par la page (fil du WebView : on repasse sur le fil principal). */
    inner class Pont {
        @JavascriptInterface
        fun requete(id: Int, chemin: String, corps: String?) {
            runOnUiThread {
                tete.http(chemin, corps) { r -> repondre(id, r.toString(), r.optBoolean("transport")) }
            }
        }

        @JavascriptInterface
        fun choisirFichiers(id: Int) {
            runOnUiThread {
                if (tete.envoiEnCours || !tete.connecte) {
                    repondre(id, JSONArray().put(JSONObject().put("ok", false)
                        .put("erreur", if (tete.connecte) "Envoi déjà en cours" else "Pas connecté à la tête")).toString(), false)
                    return@runOnUiThread
                }
                idChoix = id
                choixEnCours = true
                choixFichiers.launch(arrayOf("*/*"))
            }
        }
    }

    // --------------------------------------------------------- permissions

    private fun verifier() {
        val ok = permissions.all {
            ContextCompat.checkSelfPermission(this, it) == PackageManager.PERMISSION_GRANTED
        }
        boutonAutoriser.visibility = if (ok) View.GONE else View.VISIBLE
        if (!ok) {
            etatTexte.text = "L'appli a besoin du Bluetooth pour parler à la tête"
            return
        }
        val bm = getSystemService(BluetoothManager::class.java)
        if (bm?.adapter?.isEnabled == false) {
            try {
                @Suppress("DEPRECATION")
                startActivityForResult(Intent(BluetoothAdapter.ACTION_REQUEST_ENABLE), 1)
            } catch (_: SecurityException) {
            }
        }
        tete.demarrer()
    }

    override fun onStart() {
        super.onStart()
        val ok = permissions.all {
            ContextCompat.checkSelfPermission(this, it) == PackageManager.PERMISSION_GRANTED
        }
        if (!ok) demande.launch(permissions) else verifier()
    }

    override fun onStop() {
        super.onStop()
        // Appli en arrière-plan : on libère la tête (et la batterie)
        if (!choixEnCours) tete.arreter()
    }

    companion object {
        /** Pont JavaScript : window.teteTransport et window.teteEnvoyerFichiers. */
        private const val PONT_JS = """<script>
(function () {
  var attente = {}, n = 0;
  window.__teteReponse = function (id, texte, transport) {
    var a = attente[id]; if (!a) return; delete attente[id];
    var r; try { r = JSON.parse(texte); } catch (e) { a.non(new Error("Réponse illisible")); return; }
    if (transport) a.non(new Error(r.erreur || "Pas de liaison avec la tête")); else a.oui(r);
  };
  function appel(f) {
    return new Promise(function (oui, non) { var id = ++n; attente[id] = {oui: oui, non: non}; f(id); });
  }
  window.teteTransport = function (chemin, corps) {
    return appel(function (id) { TeteAndroid.requete(id, chemin, corps == null ? null : JSON.stringify(corps)); });
  };
  window.teteEnvoyerFichiers = function () {
    return appel(function (id) { TeteAndroid.choisirFichiers(id); });
  };
})();
</script>"""
    }
}
