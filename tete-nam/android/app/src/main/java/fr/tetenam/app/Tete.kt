package fr.tetenam.app

import android.annotation.SuppressLint
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.ParcelUuid
import android.provider.OpenableColumns
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.util.ArrayDeque
import java.util.UUID
import java.util.zip.GZIPInputStream

// Mêmes UUID que tete-nam/ble.py sur le Pi
val UUID_SERVICE: UUID = UUID.fromString("7e7e0001-7465-7465-6e61-6d2d626c6531")
val UUID_COMMANDE: UUID = UUID.fromString("7e7e0002-7465-7465-6e61-6d2d626c6531")
val UUID_REPONSE: UUID = UUID.fromString("7e7e0003-7465-7465-6e61-6d2d626c6531")
val UUID_FICHIER: UUID = UUID.fromString("7e7e0004-7465-7465-6e61-6d2d626c6531")
val UUID_CCCD: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")

/**
 * Lien Bluetooth Low Energy avec la tête : recherche, connexion, file
 * d'opérations GATT (Android n'en accepte qu'une à la fois) et requêtes JSON.
 *
 * La tête transmet chaque requête à sa page web locale : l'appli affiche la
 * même interface que l'écran de la tête et lui fait passer ses appels /api/...
 * par ici (voir MainActivity).
 *
 * Tout se passe sur le fil principal (callbacks Bluetooth renvoyés dessus).
 */
@SuppressLint("MissingPermission")   // les permissions sont demandées par MainActivity
class Tete(private val ctx: Context) {
    /** Texte d'état de la connexion, et vrai quand la tête répond. */
    var surConnexion: (texte: String, connecte: Boolean) -> Unit = { _, _ -> }

    var connecte = false
        private set
    var envoiEnCours = false
        private set

    private val main = Handler(Looper.getMainLooper())
    private val bt = ctx.getSystemService(BluetoothManager::class.java)
    private var gatt: BluetoothGatt? = null
    private var recherche = false
    private var actif = false
    private var mtu = 23
    private val file = ArrayDeque<() -> Boolean>()
    private var occupe = false
    private val recu = ByteArrayOutputStream()
    private var numero = 0
    private val attentes = HashMap<Int, (JSONObject) -> Unit>()
    // Requêtes de la page reçues pendant un envoi de fichier : parties après
    private val apresEnvoi = ArrayDeque<() -> Unit>()

    private fun annoncer(texte: String) = surConnexion(texte, connecte)

    // ------------------------------------------------------------------ cycle

    fun demarrer() {
        if (actif) return
        actif = true
        chercher()
    }

    fun arreter() {
        actif = false
        arreterRecherche()
        gatt?.close()
        gatt = null
        perdu("Bluetooth arrêté", relancer = false)
    }

    // -------------------------------------------------------------- recherche

    private val rappelRecherche = object : ScanCallback() {
        override fun onScanResult(type: Int, r: ScanResult) {
            if (!recherche) return
            arreterRecherche()
            main.post { connecter(r.device) }
        }

        override fun onScanFailed(code: Int) {
            recherche = false
            annoncer("Recherche impossible (erreur $code)")
            main.postDelayed({ if (actif && !connecte) chercher() }, 3000)
        }
    }

    private fun chercher() {
        val adaptateur = bt?.adapter
        if (adaptateur == null || !adaptateur.isEnabled) {
            annoncer("Activez le Bluetooth du téléphone")
            main.postDelayed({ if (actif && !connecte) chercher() }, 2000)
            return
        }
        val scanner = adaptateur.bluetoothLeScanner ?: return
        annoncer("Recherche de la tête…")
        recherche = true
        val filtre = ScanFilter.Builder().setServiceUuid(ParcelUuid(UUID_SERVICE)).build()
        val reglages = ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build()
        try {
            scanner.startScan(listOf(filtre), reglages, rappelRecherche)
        } catch (e: SecurityException) {
            recherche = false
            annoncer("Autorisation Bluetooth refusée")
        }
    }

    private fun arreterRecherche() {
        if (!recherche) return
        recherche = false
        try {
            bt?.adapter?.bluetoothLeScanner?.stopScan(rappelRecherche)
        } catch (_: Exception) {
        }
    }

    private fun connecter(d: BluetoothDevice) {
        if (!actif) return
        annoncer("Connexion à ${d.name ?: "la tête"}…")
        gatt?.close()
        gatt = d.connectGatt(ctx, false, rappelGatt, BluetoothDevice.TRANSPORT_LE)
    }

    private fun perdu(texte: String, relancer: Boolean = true) {
        connecte = false
        gatt?.close()
        gatt = null
        file.clear()
        occupe = false
        recu.reset()
        val enAttente = attentes.values.toList()
        attentes.clear()
        enAttente.forEach { it(erreurTransport("Connexion perdue")) }
        envoiEnCours = false
        val differes = apresEnvoi.toList()
        apresEnvoi.clear()
        differes.forEach { it() }
        annoncer(texte)
        if (relancer && actif) main.postDelayed({ if (actif && !connecte && !recherche) chercher() }, 1500)
    }

    // ------------------------------------------------------------------- GATT

    private val rappelGatt = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, nouvel: Int) {
            main.post {
                if (g != gatt) return@post
                if (nouvel == BluetoothProfile.STATE_CONNECTED) {
                    annoncer("Connecté, préparation…")
                    g.requestConnectionPriority(BluetoothGatt.CONNECTION_PRIORITY_HIGH)
                    if (!g.requestMtu(517)) g.discoverServices()
                } else {
                    perdu("Tête perdue, nouvelle recherche…")
                }
            }
        }

        override fun onMtuChanged(g: BluetoothGatt, m: Int, status: Int) {
            main.post {
                if (status == BluetoothGatt.GATT_SUCCESS) mtu = m
                g.discoverServices()
            }
        }

        override fun onServicesDiscovered(g: BluetoothGatt, status: Int) {
            main.post {
                val c = g.getService(UUID_SERVICE)?.getCharacteristic(UUID_REPONSE)
                if (c == null) {
                    perdu("Service Tête NAM introuvable sur cet appareil")
                    return@post
                }
                g.setCharacteristicNotification(c, true)
                val d = c.getDescriptor(UUID_CCCD)
                if (d == null) {
                    perdu("Notifications indisponibles sur la tête")
                    return@post
                }
                val v = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
                if (Build.VERSION.SDK_INT >= 33) {
                    g.writeDescriptor(d, v)
                } else {
                    @Suppress("DEPRECATION")
                    d.value = v
                    @Suppress("DEPRECATION")
                    g.writeDescriptor(d)
                }
            }
        }

        override fun onDescriptorWrite(g: BluetoothGatt, d: BluetoothGattDescriptor, status: Int) {
            main.post {
                connecte = true
                demander(JSONObject().put("op", "bonjour").put("mtu", mtu), 10000) { r ->
                    if (r.optBoolean("ok") && r.optInt("version") >= 2) {
                        annoncer("Connecté à ${r.optString("nom", "la tête")}")
                    } else if (r.optBoolean("ok")) {
                        perdu("Logiciel de la tête trop ancien : relancez install.sh sur la tête", relancer = false)
                    }
                }
            }
        }

        override fun onCharacteristicWrite(g: BluetoothGatt, c: BluetoothGattCharacteristic, status: Int) {
            main.post {
                occupe = false
                suivant()
            }
        }

        override fun onCharacteristicChanged(g: BluetoothGatt, c: BluetoothGattCharacteristic, valeur: ByteArray) {
            val copie = valeur.copyOf()
            main.post { recevoir(copie) }
        }

        @Deprecated("Android 12 et moins")
        override fun onCharacteristicChanged(g: BluetoothGatt, c: BluetoothGattCharacteristic) {
            if (Build.VERSION.SDK_INT >= 33) return
            @Suppress("DEPRECATION")
            val copie = c.value?.copyOf() ?: return
            main.post { recevoir(copie) }
        }
    }

    /** Ajoute une écriture à la file ; elles partent une par une. */
    private fun ecrire(uuid: UUID, octets: ByteArray) {
        file.add {
            val g = gatt ?: return@add false
            val c = g.getService(UUID_SERVICE)?.getCharacteristic(uuid) ?: return@add false
            val type = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
            if (Build.VERSION.SDK_INT >= 33) {
                g.writeCharacteristic(c, octets, type) == BluetoothGatt.GATT_SUCCESS
            } else {
                @Suppress("DEPRECATION")
                c.writeType = type
                @Suppress("DEPRECATION")
                c.value = octets
                @Suppress("DEPRECATION")
                g.writeCharacteristic(c)
            }
        }
        suivant()
    }

    private fun suivant() {
        while (!occupe && file.isNotEmpty()) {
            val op = file.poll() ?: return
            if (op()) occupe = true
        }
    }

    private fun morceaux(octets: ByteArray): List<ByteArray> {
        val taille = maxOf(20, mtu - 3)
        return (octets.indices step taille).map { octets.copyOfRange(it, minOf(it + taille, octets.size)) }
    }

    // ------------------------------------------------------ requêtes JSON

    /** Erreur de liaison (pas une erreur de la tête) : marquée "transport". */
    private fun erreurTransport(texte: String) =
        JSONObject().put("ok", false).put("erreur", texte).put("transport", true)

    private fun demander(d: JSONObject, delai: Long = 30000, rappel: (JSONObject) -> Unit) {
        if (!connecte) {
            rappel(erreurTransport("Pas connecté à la tête"))
            return
        }
        val n = ++numero
        attentes[n] = rappel
        d.put("n", n)
        morceaux((d.toString() + "\n").toByteArray()).forEach { ecrire(UUID_COMMANDE, it) }
        // Pas de réponse à temps : on abandonne cette demande
        main.postDelayed({
            attentes.remove(n)?.invoke(erreurTransport("La tête ne répond pas"))
        }, delai)
    }

    private fun recevoir(octets: ByteArray) {
        for (b in octets) {
            if (b == '\n'.code.toByte()) {
                val texte = recu.toString(Charsets.UTF_8.name())
                recu.reset()
                try {
                    var j = JSONObject(texte)
                    val n = j.optInt("n", -1)
                    if (j.has("gz")) {
                        // Réponse longue, compressée par la tête (gzip puis base64)
                        val brut = GZIPInputStream(ByteArrayInputStream(Base64.decode(j.getString("gz"), Base64.DEFAULT)))
                            .use { it.readBytes() }
                        j = JSONObject(String(brut, Charsets.UTF_8))
                    }
                    attentes.remove(n)?.invoke(j)
                } catch (_: Exception) {
                }
            } else {
                recu.write(b.toInt())
            }
        }
    }

    /** La page de l'interface (ui.html). sig = version déjà en cache. */
    fun page(sig: String?, rappel: (JSONObject) -> Unit) {
        val d = JSONObject().put("op", "page")
        if (sig != null) d.put("sig", sig)
        demander(d, 60000, rappel)
    }

    /**
     * Requête de la page vers l'API de la tête (GET si corps == null, sinon
     * POST du corps JSON). Pendant un envoi de fichier, attend la fin de l'envoi.
     */
    fun http(chemin: String, corps: String?, rappel: (JSONObject) -> Unit) {
        if (envoiEnCours) {
            apresEnvoi.add { http(chemin, corps, rappel) }
            return
        }
        val d = JSONObject().put("op", "http").put("chemin", chemin)
        if (corps != null) {
            try {
                d.put("corps", JSONObject(corps))
            } catch (_: Exception) {
                rappel(JSONObject().put("ok", false).put("erreur", "Requête illisible"))
                return
            }
        }
        demander(d, 30000) { r ->
            r.remove("n")
            rappel(r)
        }
    }

    // ---------------------------------------------------- envoi de fichiers

    /** Envoie des modèles .nam ; progression(texte) puis fin(liste de résultats). */
    fun envoyerFichiers(uris: List<Uri>, progression: (String) -> Unit, fin: (JSONArray) -> Unit) {
        val resultats = JSONArray()
        if (uris.isEmpty() || envoiEnCours) {
            fin(resultats)
            return
        }
        envoiEnCours = true
        fun termine() {
            envoiEnCours = false
            fin(resultats)
            val differes = apresEnvoi.toList()
            apresEnvoi.clear()
            differes.forEach { it() }
        }
        fun un(i: Int) {
            if (i >= uris.size || !connecte) {
                termine()
                return
            }
            val uri = uris[i]
            val nom = nomFichier(uri)
            val octets = try {
                ctx.contentResolver.openInputStream(uri)?.use { it.readBytes() }
            } catch (e: Exception) {
                null
            }
            if (octets == null) {
                resultats.put(JSONObject().put("ok", false).put("erreur", "$nom : lecture impossible"))
                un(i + 1)
                return
            }
            progression("Envoi ${i + 1}/${uris.size} : $nom…")
            demander(JSONObject().put("op", "envoi_debut").put("nom", nom)) { r ->
                if (!r.optBoolean("ok")) {
                    resultats.put(JSONObject().put("ok", false).put("erreur", "$nom : ${r.optString("erreur")}"))
                    un(i + 1)
                    return@demander
                }
                val parts = morceaux(octets)
                var faits = 0
                var dernier = -1
                parts.forEach { p ->
                    ecrire(UUID_FICHIER, p)
                    file.add {
                        // Pseudo-opération : met à jour la progression sans rien envoyer
                        faits++
                        val pc = faits * 100 / parts.size
                        if (pc / 5 != dernier / 5) {
                            dernier = pc
                            progression("Envoi ${i + 1}/${uris.size} : $nom ($pc %)")
                        }
                        false
                    }
                }
                demander(JSONObject().put("op", "envoi_fin").put("taille", octets.size), 15 * 60 * 1000L) { f ->
                    resultats.put(
                        if (f.optBoolean("ok")) JSONObject().put("ok", true).put("nom", f.optString("nom", nom) +
                            (if (f.optBoolean("remplace")) " (remplacé)" else ""))
                        else JSONObject().put("ok", false).put("erreur", "$nom : ${f.optString("erreur")}")
                    )
                    un(i + 1)
                }
            }
        }
        un(0)
    }

    private fun nomFichier(uri: Uri): String {
        ctx.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use {
            if (it.moveToFirst()) return it.getString(0)
        }
        return uri.lastPathSegment ?: "modele.nam"
    }
}
