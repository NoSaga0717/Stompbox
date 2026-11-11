/*
 * Tête NAM : générateur de sinus et mesure de niveau pour calibrer la carte son.
 *
 * Sert à trouver soi-même, avec un simple multimètre, combien de dBu donnent
 * 0 dBFS sur l'entrée de la carte son (réglage INPUT_DBU) et sur sa sortie.
 * Lancé par la page web (web.py), page Système → Mesurer la calibration.
 *
 *   tete-nam-mesure --ports
 *       liste les prises de la carte son (JSON sur une ligne)
 *   tete-nam-mesure --sortie system:playback_3 --entree system:capture_1 \
 *                   [--freq 1000] [--niveau -12]
 *       joue un sinus sur la sortie choisie et mesure l'entrée choisie.
 *   tete-nam-mesure --carre-gpio 23 --entree system:capture_1
 *       sans multimètre : fait un signal carré 0 / 3,3 V à 1 kHz sur la
 *       broche GPIO 23 du Pi (broche 16 du connecteur), à relier par une
 *       résistance de 1 kΩ à la pointe d'un jack branché dans l'entrée (masse
 *       du Pi, broche 14, sur le corps du jack). Le 3,3 V du Pi sert alors
 *       de tension de référence. Pas de sinus dans ce mode.
 *
 * Pendant la mesure, toutes les autres liaisons vers les sorties de la carte
 * sont coupées (plus de son du moteur dans les enceintes, pas de larsen par
 * la boucle sortie → entrée) puis remises à l'arrêt.
 *
 * Commandes lues sur l'entrée standard, une par ligne :
 *   f <Hz>       fréquence du sinus (20 à 20000)
 *   n <dBFS>     niveau du sinus en dBFS crête (-60 à 0)
 *   s <0|1>      couper / remettre le sinus
 *   k            « toujours là » : sans aucune ligne pendant 30 s, on s'arrête
 *   q            arrêter
 * Fin de l'entrée standard (page web arrêtée) = arrêt.
 *
 * Toutes les 0,3 s, une ligne JSON sur la sortie standard :
 *   {"lu":-18.02,"crete":-18.00,"rms":-21.03,"freq_lue":1000.0,"stable":1,...}
 *   lu    : niveau du sinus reçu, en dBFS crête, calculé à partir de la valeur
 *           efficace (+3,01 dB) : c'est la convention des fichiers NAM
 *           (« sinus de 0 dBFS crête »), et c'est moins sensible au bruit que
 *           la crête seule.
 *   crete : vraie crête mesurée. Pour un sinus propre, crete ≈ lu. Un écart
 *           de plus de 0,5 dB veut dire signal déformé (saturation) ou bruit.
 *   stable: 0 juste après un changement (on attend que tout se pose).
 *   En mode carré, en plus :
 *   carre  : demi-hauteur du carré reçu (entre ses deux paliers), en dBFS.
 *            Mesurée par les médianes des deux paliers : insensible aux
 *            oscillations des fronts, au rapport cyclique et aux à-coups
 *            de cadence.
 *   nettete: part des échantillons posés sur un des deux paliers (0 à 1).
 *            Un vrai carré bien reçu donne plus de 0,7.
 *
 * Compilation : gcc -O2 -o tete-nam-mesure mesure-niveau.c -ljack -lm -lpthread
 */
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <jack/jack.h>
#include <linux/gpio.h>
#include <pthread.h>
#include <sched.h>
#include <sys/ioctl.h>
#include <math.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <time.h>
#include <unistd.h>

#define MAX_LIAISONS 256
#define FENETRE_S 0.5          /* durée d'une mesure */
#define ATTENTE_S 0.35         /* temps de pose après un changement */
#define SANS_NOUVELLES_S 30.0  /* arrêt automatique sans commande */

static jack_client_t *client;
static jack_port_t *port_sortie, *port_entree;
static double frequence = 1000.0, phase = 0.0;
static _Atomic double frequence_voulue = 1000.0;
static _Atomic float gain_voulu = 0.0f;   /* amplitude crête, 0..1 */
static _Atomic int son = 1;
static float gain = 0.0f;                 /* suit gain_voulu sans à-coup */
static _Atomic int arret = 0;
static _Atomic int jack_parti = 0;

/* Mesure : accumulée par le fil audio, publiée à la fin de chaque fenêtre */
typedef struct {
    double somme, somme2;      /* pour la valeur efficace sans le continu */
    double crete;
    long n;
    long passages;             /* passages par zéro montants (fréquence) */
    int stable;                /* fenêtre entière après le temps de pose */
} Mesure;
static Mesure acc;
static int signe_prec = 0;
static long taille_fenetre, a_jeter;
/* deux cases : le fil audio remplit l'une pendant qu'on lit l'autre */
static Mesure publiee[2];
static float *echantillons[2];           /* les échantillons de chaque fenêtre */
static int mode_carre = 0;
static _Atomic int numero_publie = 0;    /* +1 à chaque fenêtre publiée */
static _Atomic int changement = 0;       /* fixé par le fil principal */
static int stable_en_cours = 0;

/* Liaisons coupées pendant la mesure, remises à la fin */
static char *liaison_src[MAX_LIAISONS], *liaison_dst[MAX_LIAISONS];
static int nb_liaisons = 0;

static void sortir_json_chaine(const char *s)
{
    putchar('"');
    for (; *s; s++) {
        if (*s == '"' || *s == '\\') putchar('\\');
        if ((unsigned char)*s >= 0x20) putchar(*s);
    }
    putchar('"');
}

static double db(double v) { return v > 1e-12 ? 20.0 * log10(v) : -240.0; }

/* ---- Signal carré sur une broche GPIO (mode sans multimètre) ---- */
static int gpio_fd = -1;
static pthread_t fil_gpio;

static void gpio_ecrire(int v)
{
    struct gpio_v2_line_values val = {.bits = v ? 1 : 0, .mask = 1};
    if (gpio_fd >= 0) ioctl(gpio_fd, GPIO_V2_LINE_SET_VALUES_IOCTL, &val);
}

/* Ouvre la broche en sortie sur la puce GPIO du connecteur 40 broches
   (« pinctrl-rp1 » sur Pi 5, « pinctrl-bcm2711/2835 » sur les Pi plus anciens). */
static const char *gpio_ouvrir(int broche)
{
    DIR *d = opendir("/dev");
    struct dirent *e;
    const char *erreur = "Pas de puce GPIO du Pi (/dev/gpiochip*)";
    while (d && (e = readdir(d))) {
        if (strncmp(e->d_name, "gpiochip", 8)) continue;
        char chemin[300];
        snprintf(chemin, sizeof chemin, "/dev/%s", e->d_name);
        int fd = open(chemin, O_RDWR | O_CLOEXEC);
        if (fd < 0) { erreur = "Accès GPIO refusé (l'utilisateur doit être dans le groupe gpio)"; continue; }
        struct gpiochip_info info = {0};
        if (ioctl(fd, GPIO_GET_CHIPINFO_IOCTL, &info) || strncmp(info.label, "pinctrl-", 8) ||
            (unsigned)broche >= info.lines) {
            close(fd);
            continue;
        }
        struct gpio_v2_line_request req = {0};
        req.offsets[0] = broche;
        req.num_lines = 1;
        req.config.flags = GPIO_V2_LINE_FLAG_OUTPUT;
        snprintf(req.consumer, sizeof req.consumer, "tete-nam-mesure");
        int r = ioctl(fd, GPIO_V2_GET_LINE_IOCTL, &req);
        close(fd);
        if (r) { erreur = "Broche GPIO occupée ou refusée"; continue; }
        gpio_fd = req.fd;
        closedir(d);
        return NULL;
    }
    if (d) closedir(d);
    return erreur;
}

/* 1 kHz : 0,5 ms à 3,3 V puis 0,5 ms à 0 V. Une cadence un peu irrégulière
   ne gêne pas : on mesure la hauteur des paliers, pas leur durée. */
static void *boucle_gpio(void *arg)
{
    (void)arg;
    struct sched_param sp = {.sched_priority = 10};
    pthread_setschedparam(pthread_self(), SCHED_FIFO, &sp);   /* si permis */
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    int v = 0;
    while (!atomic_load(&arret)) {
        v = !v;
        gpio_ecrire(v);
        t.tv_nsec += 500000;
        if (t.tv_nsec >= 1000000000) { t.tv_nsec -= 1000000000; t.tv_sec++; }
        clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &t, NULL);
    }
    gpio_ecrire(0);
    return NULL;
}

static int comparer(const void *a, const void *b)
{
    float x = *(const float *)a, y = *(const float *)b;
    return (x > y) - (x < y);
}

/* Médianes des deux paliers d'un carré : demi-hauteur et netteté */
static void analyser_carre(const float *x, long n, double moy, double *demi, double *nettete)
{
    float *haut = malloc(n * sizeof(float)), *bas = malloc(n * sizeof(float));
    long nh = 0, nb = 0;
    for (long i = 0; i < n; i++) {
        if (x[i] > moy) haut[nh++] = x[i];
        else bas[nb++] = x[i];
    }
    *demi = 0;
    *nettete = 0;
    if (nh > 10 && nb > 10) {
        qsort(haut, nh, sizeof(float), comparer);
        qsort(bas, nb, sizeof(float), comparer);
        double mh = haut[nh / 2], mb = bas[nb / 2], tol = 0.03 * (mh - mb);
        *demi = (mh - mb) / 2;
        long poses = 0;
        for (long i = 0; i < n; i++)
            if (fabs(x[i] - mh) <= tol || fabs(x[i] - mb) <= tol) poses++;
        *nettete = (double)poses / n;
    }
    free(haut);
    free(bas);
}

static int traiter(jack_nframes_t n, void *arg)
{
    (void)arg;
    float *out = jack_port_get_buffer(port_sortie, n);
    const float *in = jack_port_get_buffer(port_entree, n);
    double f = atomic_load(&frequence_voulue);
    float cible = atomic_load(&son) && !atomic_load(&arret) ? atomic_load(&gain_voulu) : 0.0f;
    double fe = jack_get_sample_rate(client);
    /* gain lissé sur ~10 ms : pas de clic quand on change le niveau */
    float k = (float)(1.0 - exp(-1.0 / (0.010 * fe)));

    if (atomic_exchange(&changement, 0)) {
        acc = (Mesure){0};
        a_jeter = (long)(ATTENTE_S * fe);
        stable_en_cours = 0;
    }
    frequence = f;
    double pas = 2.0 * M_PI * frequence / fe;
    for (jack_nframes_t i = 0; i < n; i++) {
        gain += k * (cible - gain);
        out[i] = (float)(gain * sin(phase));
        phase += pas;
        if (phase >= 2.0 * M_PI) phase -= 2.0 * M_PI;

        if (a_jeter > 0) { a_jeter--; continue; }
        double x = in[i];
        if (mode_carre) echantillons[(atomic_load(&numero_publie) + 1) & 1][acc.n] = (float)x;
        acc.somme += x;
        acc.somme2 += x * x;
        if (fabs(x) > acc.crete) acc.crete = fabs(x);
        /* passage par zéro montant, avec un peu d'hystérésis contre le bruit */
        int s = x > 1e-4 ? 1 : x < -1e-4 ? -1 : 0;
        if (s) {
            if (s > 0 && signe_prec < 0) acc.passages++;
            signe_prec = s;
        }
        if (++acc.n >= taille_fenetre) {
            int suivant = atomic_load(&numero_publie) + 1;
            acc.stable = stable_en_cours;
            publiee[suivant & 1] = acc;
            atomic_store(&numero_publie, suivant);
            acc = (Mesure){0};
            stable_en_cours = 1;
        }
    }
    return 0;
}

static void jack_arrete(void *arg)
{
    (void)arg;
    atomic_store(&jack_parti, 1);
    atomic_store(&arret, 1);
}

static void sur_signal(int s)
{
    (void)s;
    atomic_store(&arret, 1);
}

static void lister_ports(void)
{
    const char **cap = jack_get_ports(client, NULL, JACK_DEFAULT_AUDIO_TYPE, JackPortIsPhysical | JackPortIsOutput);
    const char **sor = jack_get_ports(client, NULL, JACK_DEFAULT_AUDIO_TYPE, JackPortIsPhysical | JackPortIsInput);
    printf("{\"ok\":true,\"captures\":[");
    for (int i = 0; cap && cap[i]; i++) { if (i) putchar(','); sortir_json_chaine(cap[i]); }
    printf("],\"sorties\":[");
    for (int i = 0; sor && sor[i]; i++) { if (i) putchar(','); sortir_json_chaine(sor[i]); }
    printf("],\"frequence\":%u}\n", jack_get_sample_rate(client));
    jack_free(cap);
    jack_free(sor);
}

/* Coupe tout ce qui arrive sur les sorties de la carte (sauf nous) */
static void couper_sorties(void)
{
    const char **sor = jack_get_ports(client, NULL, JACK_DEFAULT_AUDIO_TYPE, JackPortIsPhysical | JackPortIsInput);
    for (int i = 0; sor && sor[i]; i++) {
        jack_port_t *p = jack_port_by_name(client, sor[i]);
        const char **src = p ? jack_port_get_all_connections(client, p) : NULL;
        for (int j = 0; src && src[j]; j++) {
            if (strcmp(src[j], jack_port_name(port_sortie)) == 0) continue;
            if (nb_liaisons < MAX_LIAISONS) {
                liaison_src[nb_liaisons] = strdup(src[j]);
                liaison_dst[nb_liaisons] = strdup(sor[i]);
                nb_liaisons++;
            }
            jack_disconnect(client, src[j], sor[i]);
        }
        jack_free(src);
    }
    jack_free(sor);
}

static void remettre_sorties(void)
{
    for (int i = 0; i < nb_liaisons; i++) {
        int r = jack_connect(client, liaison_src[i], liaison_dst[i]);
        if (r && r != EEXIST)
            fprintf(stderr, "liaison %s -> %s non remise (%d)\n", liaison_src[i], liaison_dst[i], r);
    }
}

static double maintenant(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec * 1e-9;
}

static double borne(double v, double mini, double maxi) { return v < mini ? mini : v > maxi ? maxi : v; }

static void regler_niveau(double dbfs) { atomic_store(&gain_voulu, (float)pow(10.0, borne(dbfs, -60, 0) / 20.0)); }

int main(int argc, char **argv)
{
    const char *sortie = NULL, *entree = NULL;
    double niveau = -12.0;
    int ports = 0, broche = -1;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--ports")) ports = 1;
        else if (!strcmp(argv[i], "--sortie") && i + 1 < argc) sortie = argv[++i];
        else if (!strcmp(argv[i], "--entree") && i + 1 < argc) entree = argv[++i];
        else if (!strcmp(argv[i], "--freq") && i + 1 < argc) frequence = borne(atof(argv[++i]), 20, 20000);
        else if (!strcmp(argv[i], "--niveau") && i + 1 < argc) niveau = atof(argv[++i]);
        else if (!strcmp(argv[i], "--carre-gpio") && i + 1 < argc) broche = atoi(argv[++i]);
        else {
            fprintf(stderr, "usage : %s --ports | --sortie PORT --entree PORT [--freq Hz] [--niveau dBFS]\n"
                            "        | --carre-gpio BROCHE --entree PORT\n", argv[0]);
            return 2;
        }
    }
    mode_carre = broche >= 0;
    if (!ports && (!entree || (!sortie && !mode_carre))) {
        fprintf(stderr, "--entree et --sortie (ou --carre-gpio) sont obligatoires\n");
        return 2;
    }

    jack_status_t st;
    client = jack_client_open("tete-nam-mesure", JackNoStartServer, &st);
    if (!client) {
        printf("{\"ok\":false,\"erreur\":\"Le serveur audio JACK ne tourne pas\"}\n");
        return 1;
    }
    if (ports) {
        lister_ports();
        jack_client_close(client);
        return 0;
    }

    atomic_store(&frequence_voulue, frequence);
    regler_niveau(niveau);
    taille_fenetre = (long)(FENETRE_S * jack_get_sample_rate(client));
    if (mode_carre) {
        /* TETE_NAM_MESURE_SANS_GPIO=1 : essais sur PC, le carré vient d'ailleurs */
        const char *err = getenv("TETE_NAM_MESURE_SANS_GPIO") ? NULL : gpio_ouvrir(broche);
        if (err) {
            printf("{\"ok\":false,\"erreur\":\"%s\"}\n", err);
            jack_client_close(client);
            return 1;
        }
        atomic_store(&son, 0);
        echantillons[0] = calloc(taille_fenetre + 1, sizeof(float));
        echantillons[1] = calloc(taille_fenetre + 1, sizeof(float));
    }
    a_jeter = (long)(ATTENTE_S * jack_get_sample_rate(client));

    port_sortie = jack_port_register(client, "sinus", JACK_DEFAULT_AUDIO_TYPE, JackPortIsOutput, 0);
    port_entree = jack_port_register(client, "mesure", JACK_DEFAULT_AUDIO_TYPE, JackPortIsInput, 0);
    if (!port_sortie || !port_entree) {
        printf("{\"ok\":false,\"erreur\":\"Ports JACK impossibles à créer\"}\n");
        return 1;
    }
    jack_set_process_callback(client, traiter, NULL);
    jack_on_shutdown(client, jack_arrete, NULL);
    signal(SIGTERM, sur_signal);
    signal(SIGINT, sur_signal);
    signal(SIGHUP, sur_signal);
    signal(SIGPIPE, sur_signal);

    if (jack_activate(client)) {
        printf("{\"ok\":false,\"erreur\":\"Client JACK impossible à activer\"}\n");
        return 1;
    }
    if ((sortie && !jack_port_by_name(client, sortie)) || !jack_port_by_name(client, entree)) {
        printf("{\"ok\":false,\"erreur\":\"Prise %s ou %s introuvable\"}\n", sortie ? sortie : "-", entree);
        jack_client_close(client);
        return 1;
    }
    couper_sorties();
    if ((sortie && jack_connect(client, jack_port_name(port_sortie), sortie)) ||
        jack_connect(client, entree, jack_port_name(port_entree))) {
        printf("{\"ok\":false,\"erreur\":\"Liaison vers %s ou depuis %s impossible\"}\n", sortie ? sortie : "-", entree);
        remettre_sorties();
        jack_client_close(client);
        return 1;
    }
    if (mode_carre && gpio_fd >= 0) pthread_create(&fil_gpio, NULL, boucle_gpio, NULL);
    printf("{\"ok\":true,\"debut\":1,\"frequence\":%u,\"coupees\":%d}\n", jack_get_sample_rate(client), nb_liaisons);
    fflush(stdout);

    char ligne[256];
    size_t lus = 0;
    double derniere_commande = maintenant();
    int numero_vu = atomic_load(&numero_publie);
    while (!atomic_load(&arret)) {
        fd_set fs;
        FD_ZERO(&fs);
        FD_SET(0, &fs);
        struct timeval tv = {0, 50000};
        if (select(1, &fs, NULL, NULL, &tv) > 0) {
            ssize_t r = read(0, ligne + lus, sizeof ligne - 1 - lus);
            if (r <= 0) break;                       /* page web partie */
            lus += r;
            ligne[lus] = 0;
            char *fin;
            while ((fin = strchr(ligne, '\n'))) {
                *fin = 0;
                double v = 0;
                derniere_commande = maintenant();
                if (mode_carre && (ligne[0] == 'f' || ligne[0] == 'n' || ligne[0] == 's')) {
                    /* pas de sinus en mode carré */
                } else if (sscanf(ligne, "f %lf", &v) == 1) {
                    atomic_store(&frequence_voulue, borne(v, 20, 20000));
                    atomic_store(&changement, 1);
                } else if (sscanf(ligne, "n %lf", &v) == 1) {
                    regler_niveau(v);
                    niveau = borne(v, -60, 0);
                    atomic_store(&changement, 1);
                } else if (sscanf(ligne, "s %lf", &v) == 1) {
                    atomic_store(&son, v != 0);
                    atomic_store(&changement, 1);
                } else if (ligne[0] == 'q') {
                    atomic_store(&arret, 1);
                }
                memmove(ligne, fin + 1, strlen(fin + 1) + 1);
                lus = strlen(ligne);
            }
            if (lus >= sizeof ligne - 1) lus = 0;   /* ligne trop longue : on jette */
        }
        if (maintenant() - derniere_commande > SANS_NOUVELLES_S) break;

        int numero = atomic_load(&numero_publie);
        if (numero != numero_vu) {
            numero_vu = numero;
            Mesure m = publiee[numero & 1];           /* le fil audio écrit dans l'autre case */
            if (m.n <= 0) continue;
            double moy = m.somme / m.n;
            double var = m.somme2 / m.n - moy * moy;
            double rms = sqrt(var > 0 ? var : 0);
            double fe = jack_get_sample_rate(client);
            printf("{\"lu\":%.3f,\"crete\":%.3f,\"rms\":%.3f,\"continu\":%.6f,\"freq_lue\":%.1f,"
                   "\"freq\":%.1f,\"niveau\":%.2f,\"son\":%d,\"stable\":%d",
                   db(rms * M_SQRT2), db(m.crete), db(rms), moy, m.passages * fe / m.n,
                   mode_carre ? 1000.0 : atomic_load(&frequence_voulue), niveau, atomic_load(&son), m.stable);
            if (mode_carre) {
                double demi, nettete;
                analyser_carre(echantillons[numero & 1], m.n, moy, &demi, &nettete);
                printf(",\"carre\":%.3f,\"nettete\":%.3f", db(demi), nettete);
            }
            printf("}\n");
            fflush(stdout);
        }
    }

    /* fondu de sortie puis on remet les liaisons du moteur */
    atomic_store(&arret, 1);
    if (mode_carre && gpio_fd >= 0) {
        pthread_join(fil_gpio, NULL);
        close(gpio_fd);
    }
    if (!atomic_load(&jack_parti)) {
        usleep(80000);
        remettre_sorties();
        jack_client_close(client);
    }
    return 0;
}
