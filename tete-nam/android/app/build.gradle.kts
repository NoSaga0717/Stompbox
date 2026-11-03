plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "fr.tetenam.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "fr.tetenam.app"
        minSdk = 26
        targetSdk = 35
        // La CI passe le numéro de build : chaque APK peut s'installer par-dessus le précédent.
        // Version 2 = interface de la tête (stompbox) affichée dans l'appli
        val build = (System.getenv("NUMERO_BUILD") ?: "0").toInt()
        versionCode = 1000 + build
        versionName = "2.0.$build"
    }

    // Clé de signature fixe, pour que chaque mise à jour s'installe par-dessus la
    // précédente. Elle n'est PAS dans le dépôt : la CI la reçoit des secrets GitHub
    // TETE_NAM_KEYSTORE (fichier en base64) et TETE_NAM_KEYSTORE_MDP. Sans eux,
    // l'APK est signé avec une clé de debug jetable (à désinstaller à chaque mise à jour).
    val cle = System.getenv("TETE_NAM_KEYSTORE")?.takeIf { file(it).exists() }
    signingConfigs {
        if (cle != null) {
            create("tete") {
                storeFile = file(cle)
                storePassword = System.getenv("TETE_NAM_KEYSTORE_MDP")
                keyAlias = "tetenam"
                keyPassword = System.getenv("TETE_NAM_KEYSTORE_MDP")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName(if (cle != null) "tete" else "debug")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation("androidx.core:core-ktx:1.15.0")
}
