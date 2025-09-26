# Séquenceur MIDI Interactif en Python

Ce projet est un séquenceur MIDI interactif en ligne de commande, écrit en Python. Il vous permet d'ajouter des pistes, de charger des fichiers MIDI, d'enregistrer depuis un périphérique MIDI en temps réel et de sauvegarder votre travail.

## Installation (Linux)

1.  **Clonez le projet** (si ce n'est pas déjà fait) et naviguez dans le répertoire :
    ```bash
    git clone <repository_url>
    cd <repository_directory>
    ```

2.  **Créez et activez un environnement virtuel** :
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```

3.  **Installez les dépendances** :
    Cette commande installe toutes les dépendances Python nécessaires, y compris celles pour l'interface graphique (`kivy`, `kivymd`).
    ```bash
    pip install -r requirements.txt
    ```

## Utilisation

Une fois le projet installé, lancez l'interface en ligne de commande avec :
```bash
python3 main.py
```
Vous verrez un message de bienvenue et une invite `>`. Tapez `help` pour voir la liste des commandes.

Pour afficher l'aide sans lancer le séquenceur, vous pouvez utiliser :
```bash
python3 main.py --help
```

## Mode API

Le séquenceur peut être lancé en "Mode API", ce qui est utile pour l'intégrer à d'autres applications ou pour le piloter par des scripts.

Pour activer le mode API, utilisez l'argument `--api` :
```bash
python3 main.py --api
```

En mode API :
- Le séquenceur lit les commandes depuis l'entrée standard (`stdin`), une par ligne.
- Les réponses sont envoyées sur la sortie standard (`stdout`) au format JSON.
- Le prompt `>` n'est pas affiché.

C'est le mode idéal pour une utilisation programmatique.

## Interface Graphique (KivyMD)

En plus de l'interface en ligne de commande, ce séquenceur dispose d'une interface graphique développée avec Kivy et KivyMD, offrant une expérience utilisateur plus moderne avec des icônes et des thèmes.

### Dépendances supplémentaires

Pour utiliser l'interface graphique, vous aurez besoin de dépendances système supplémentaires. Sur un système basé sur Debian (comme Ubuntu), installez-les avec :

```bash
sudo apt-get update
sudo apt-get install -y libjack-jackd2-dev libasound2-dev libmtdev-dev
```

Les dépendances Python, y compris `kivy` et `kivymd`, sont gérées par le fichier `requirements.txt` et installées avec la commande `pip install -r requirements.txt` mentionnée dans la section d'installation principale.

### Lancement de l'interface graphique

Pour lancer l'application avec l'interface graphique, utilisez la commande suivante :

```bash
KIVY_NO_ARGS=1 python3 main.py --gui
```

L'interface vous présentera une zone de texte pour entrer les commandes et une zone d'affichage pour voir les réponses du séquenceur.

**Note :** L'intégration de l'interface graphique est encore en cours de développement. Les commandes nécessitant une confirmation de l'utilisateur (comme la suppression de pistes) ne sont pas encore entièrement fonctionnelles dans l'interface graphique.

### Format des réponses JSON

Toutes les réponses JSON suivent ce format de base :
```json
{
  "status": "success" | "error" | "prompt" | "cancelled",
  "message": "Description textuelle du résultat."
  // ... autres champs si nécessaire
}
```

-   **`status: "success"`** : La commande a été exécutée avec succès.
-   **`status: "error"`** : Une erreur est survenue. Le message contient les détails.
-   **`status: "cancelled"`** : La commande a été annulée par l'utilisateur (par exemple, en ne confirmant pas une suppression).
-   **`status: "prompt"`** : La commande est interactive et attend une information supplémentaire.
    -   Le champ `message` contient le texte à afficher à l'utilisateur.
    -   Un champ `next_arg` indique le nom de l'argument attendu pour la prochaine commande.

**Exemple de dialogue pour une commande interactive (`transpose`) :**

1.  **Client envoie :** `transpose 0`
2.  **Séquenceur répond (prompt) :**
    ```json
    {
      "status": "prompt",
      "message": "Transpose from position on track 'piano' (measure:beat) [default: 1:1]: ",
      "next_arg": "start_pos_str"
    }
    ```
3.  **Client envoie :** `transpose 0 1:1`
4.  **Séquenceur répond (prompt) :**
    ```json
    {
      "status": "prompt",
      "message": "Transpose up to position on track 'piano' (measure:beat) [default: end of track]: ",
      "next_arg": "end_pos_str"
    }
    ```
5.  ... et ainsi de suite jusqu'à ce que la commande soit complète.

## Commandes disponibles

```
Sequencer CLI Commands:
  help                    - Shows this help message.
  add <name> [prog]       - Adds a new MIDI track. `prog` is an optional program number (1-128).
  addaudio <name> <path>  - Adds a new Audio track with the audio file at <path>.
  addauto <name> <target_idx> - Adds an automation track targeting another track.
  addap <track> <pos> <p> <val> [curve] - Adds an automation point. Curves: none, linear, ease-in, ease-out, ease-in-out, sine.
  addcc <track> <pos> <cc> <val> - Adds a CC event to a track at a 'measure:beat' position.
  load <filepath>         - Loads a song from a MIDI file.
  loadproject <basename>  - Loads a full project (MIDI, vports, assignments).
  newproject <name>       - Creates a new, empty project.
  list                    - Shows all tracks in the current song.
  ports                   - Lists available MIDI input and output ports.
  vport <name>            - Creates a virtual MIDI output port.
  delvport                - Deletes an existing virtual port.
  assign <track_index>    - Assigns a track to an output port from a list of choices.
  assignmetro             - Assigns an output port for the metronome click.
  unassign <track_index>  - Un-assigns a track from its output port.
  setaudiocmd <cmd...>    - Sets the command for the external audio player (e.g., mpv --audio-device=jack).
  setbank <track> <msb> [lsb] - Sets the MIDI bank for a track (MSB=CC0, LSB=CC32).
  setch <track> <ch>      - Sets the MIDI channel (1-16) for a track.
  setprog <track> <prog>  - Sets the MIDI program (1-128) for a track.
  volume <track_index>    - Sets the volume for an audio or MIDI track (0.0 to 1.0).
  pan <track_index>       - Sets the pan for an audio or MIDI track (-1.0 to 1.0).
  velocity <track_index>  - Sets the velocity multiplier for a MIDI track (e.g., 1.0).
  mute <track_index>      - Toggles mute for a track.
  solo <track_index>      - Toggles solo for a track.
  rename <index> <new_name> - Renames a track.
  copy <track_index>      - Copies a section of a track using 'measure:beat' positions.
  move <track_index>      - Moves a section of a track using 'measure:beat' positions.
  transpose <track_index> - Transposes a section of a track using 'measure:beat' positions.
  record <track_index>    - Records MIDI to a track, with 'measure:beat' precision.
  bis                     - Re-records with the last used 'record' settings.
  delete <track_index>    - Deletes a track after confirmation.
  erase <track_index>     - Erases notes from a track using 'measure:beat' positions.
  tempo <bpm>             - Sets the song tempo in beats per minute.
  timesig <num> <den>     - Sets the song time signature (e.g., 4 4).
  save <filepath>         - Saves only the song to a MIDI file.
  saveproject <basename>  - Saves the full project (MIDI, vports, assignments).
  prime                   - Sends current program/bank state to all assigned ports.
  cc                      - Sends a single MIDI CC message to a port.
  play [start] [end]      - Plays the song. Start/end positions are in 'measure:beat'.
  loop [start] [end]      - Loops a section of the song. Start/end positions are in 'measure:beat'.
  pause                   - Pauses or resumes playback.
  stop                    - Stops playback.
  restart                 - Stops and restarts playback from the beginning.
  metronome <on|off>      - Enables or disables the metronome.
  quit                    - Exits the sequencer.

MIDI Mapping:
  setcontrolport <port>   - Sets the MIDI input port for control messages.
  unsetcontrolport        - Unsets the MIDI control port.
  map <chan> <cc> <track> <action> - Maps a MIDI CC to an action (volume, pan, program).
  unmap <chan> <cc>       - Removes a MIDI CC mapping.
  listmaps                - Lists all active MIDI CC mappings.
```

---

## Sauvegarde et Chargement de Projets

Pour éviter de reconfigurer vos ports virtuels et vos assignations de pistes à chaque session, vous pouvez utiliser les commandes de projet.

-   **`saveproject <nom>`** : Cette commande sauvegarde deux fichiers :
    1.  `<nom>.mid` : Le fichier MIDI standard contenant toutes vos notes.
    2.  `<nom>.proj.json` : Un fichier de configuration qui mémorise les ports virtuels que vous avez créés, les assignations de pistes, le chemin des pistes audio, le port de contrôle, les mappings MIDI et d'autres réglages.

-   **`loadproject <nom>`** : Cette commande charge un projet complet. Elle va :
    1.  Lire le fichier `<nom>.proj.json`.
    2.  Charger le fichier MIDI associé.
    3.  Recréer automatiquement les ports virtuels.
    4.  Réassigner les pistes aux bons ports.
    5.  Restaurer le port de contrôle et les mappings MIDI.

---

## Exemples d'utilisation

### How-To : Configurer et enregistrer une piste MIDI

Voici un exemple de workflow complet pour une piste MIDI.

1.  **Ajouter une piste :**
    *   `> add piano` (ajoute une piste nommée "piano" avec le programme 1 par défaut)

2.  **Configurer l'instrument et la chanson :**
    *   Renommer la piste si nécessaire : `> rename 0 "Grand Piano"`
    *   Changer le programme pour un piano électrique (ex: 5) : `> setprog 0 5`
    *   Changer le canal MIDI pour le canal 10 : `> setch 0 10`
    *   Définir la banque de sons (ex: MSB=1, LSB=1) : `> setbank 0 1 1`
    *   Définir la signature rythmique : `> timesig 3 4`
    *   Ajuster le volume (ex: 0.8) et la vélocité (ex: 1.2) : `> volume 0` (entrez 0.8), `> velocity 0` (entrez 1.2)

3.  **Lister les pistes** pour vérifier la configuration : `list`
    ```
    Song: New Song | Tempo: 120 BPM | Time Signature: 3/4
    ====================
    [0] Grand Piano (Ch: 10, Prog: 5, Bank: 1:1, Vol: 0.8, Vel: 1.2, 0 events)
    ```

4.  **Connecter à un synthétiseur (via Carla) :**
    *   Créer un port virtuel : `> vport mon-synth`
    *   Lancer Carla, y charger un synthétiseur, et le configurer pour qu'il écoute sur le **canal 10**.
    *   Dans la baie de patch de Carla, connecter la sortie `mon-synth` à l'entrée du synthétiseur.
    *   Assigner la piste au port virtuel : `> assign 0` -> choisir `mon-synth` dans la liste.

5.  **Envoyer la configuration au synthétiseur :**
    *   La commande `assign` n'envoie pas automatiquement l'état. Utilisez la commande `prime` pour mettre à jour votre synthétiseur.
    *   `> prime`

6.  **Enregistrer la piste en s'écoutant en direct :**
    *   Activer le métronome : `> metronome on`
    *   Lancer l'enregistrement : `> record 0`
    *   Choisir votre clavier physique comme port d'entrée.
    *   Le MIDI Thru est activé par défaut pour l'overdubbing, vous devriez entendre votre synthé en jouant.
    *   Jouez ! Vous entendrez le son du synthé de Carla pendant l'enregistrement.

7.  **Sauvegarder le projet :**
    *   `> saveproject mon_morceau`

8.  Plus tard, vous pourrez tout recharger avec `loadproject mon_morceau`.

---

### How-To : Utiliser une piste Audio

Le séquenceur peut également gérer des pistes audio, lues par un lecteur externe comme `mpv` ou `ffplay`.

1.  **Configurer le lecteur audio :**
    *   Indiquez au séquenceur quelle commande lancer pour jouer un fichier audio.
    *   `> setaudiocmd mpv --no-video`
    *   Le chemin du fichier audio sera ajouté à la fin de cette commande.

2.  **Ajouter une piste audio :**
    *   Ajoutez un fichier audio (ex: une boucle de batterie) au projet.
    *   `> addaudio drums /chemin/vers/ma/boucle.wav`

3.  **Ajuster le volume :**
    *   Le volume des pistes audio peut être contrôlé.
    *   `> volume 1` (entrez 0.7 pour baisser le volume)

4.  **Jouer le projet :**
    *   `> play`
    *   Le séquenceur lancera `mpv --no-video /chemin/vers/ma/boucle.wav` en même temps que la lecture MIDI.

---

### How-To : Éditer une piste

Une fois que vous avez enregistré des notes, vous pouvez les manipuler avec précision.

1.  **Copier une section :**
    *   Copier les deux premières mesures de la piste 0 pour les coller à partir de la mesure 5.
    *   `> copy`
    *   Suivez les invites pour définir la source (piste, début, fin) et la destination.

2.  **Déplacer une section :**
    *   Déplacer la mesure 5 de la piste 0 pour la mettre à la mesure 10.
    *   `> move 0`
    *   Suivez les invites pour définir la source et la destination.

3.  **Effacer des notes :**
    *   Effacer les notes de la première mesure de la piste 0.
    *   `> erase 0`
    *   Suivez les invites :
        *   `Erase from position...: 1:1`
        *   `Erase up to position...: 2:1`

4.  **Transposer une section :**
    *   Transposer toute la piste 0 d'une octave vers le haut (12 demi-tons).
    *   `> transpose`
    *   Suivez les invites pour définir la piste, la plage et le nombre de demi-tons.

---

### How-To : Utiliser les Pistes d'Automation

L'automation permet de faire évoluer un paramètre au fil du temps. Les paramètres supportés sont : `vol`, `pan`, `vel`, `prog`, et les CC génériques (`cc0` à `cc127`).

1.  **Créez vos pistes :**
    *   `> add piano` (crée la piste MIDI 0)
    *   `> addauto "Piano Automation" 0` (crée une piste d'automation qui cible la piste 0)

2.  **Affichez la liste des pistes** pour vérifier : `list`
    ```
    [0] piano (MIDI) ...
    [1] Piano Automation (Automation) (Target: 0 'piano', 0 points)
    ```

3.  **Créez des points d'automation :**
    *   Chaque point est défini par une position, un paramètre, une valeur et une courbe (`none`, `linear`, `ease-in`, `ease-out`, `ease-in-out`, `sine`).
    *   La courbe définit la transition *vers le point suivant*. Le dernier point d'une séquence doit utiliser `none`.

    *   **Exemple 1 : Fondu de volume (vol) avec une courbe `ease-in`**
        *   `> addap 1 1:1 vol 0.0 ease-in`  (Démarre à 0, commence lentement)
        *   `> addap 1 3:1 vol 1.0 none`      (Atteint 1.0 à la mesure 3)

    *   **Exemple 2 : Panoramique (pan) de gauche à droite**
        *   `> addap 1 3:1 pan -1.0 linear` (Commence à gauche à la mesure 3)
        *   `> addap 1 5:1 pan 1.0 none`    (Atteint la droite à la mesure 5)

    *   **Exemple 3 : Changement de programme (prog) au milieu d'une mesure**
        *   `> addap 1 5:3 prog 24 none` (Passe au programme 25 à 5:3)
        *   *Note : la valeur du programme est 0-127, donc 24 correspond au programme 25.*

    *   **Exemple 4 : Automation de la molette de modulation (cc1)**
        *   `> addap 1 6:1 cc1 0 ease-in-out` (Module à 0 à la mesure 6)
        *   `> addap 1 7:1 cc1 127 none`        (Atteint 127 à la mesure 7)

4.  **Jouez la piste :**
    *   `> play`
    *   Le séquenceur calcule automatiquement toutes les étapes intermédiaires pour créer des transitions fluides selon les courbes choisies.

---

### How-To : Utiliser le MIDI Mapping

Le MIDI Mapping vous permet d'utiliser un contrôleur externe (comme un clavier avec des faders ou des potentiomètres) pour contrôler en temps réel les paramètres du séquenceur, comme le volume, le panoramique ou le programme d'une piste.

1.  **Lister les ports d'entrée MIDI :**
    *   D'abord, identifiez le port de votre contrôleur.
    *   `> ports`

2.  **Définir le port de contrôle :**
    *   Indiquez au séquenceur d'écouter sur ce port pour les messages de contrôle.
    *   `> setcontrolport <port_index>`
    *   Exemple : `> setcontrolport 1`

3.  **Mapper un contrôle CC à une action :**
    *   La commande `map` lie un numéro de CC (Control Change) sur un canal MIDI spécifique à une action sur une piste.
    *   Format : `map <canal> <cc> <piste> <action>`
    *   Actions valides : `volume`, `pan`, `program`.

    *   **Exemple 1 : Mapper le CC#7 (volume) du canal 1 au volume de la piste 0**
        *   `> map 1 7 0 volume`
        *   Maintenant, bouger le fader ou le potentiomètre qui envoie le CC#7 sur le canal 1 changera le volume de la piste 0 en temps réel.

    *   **Exemple 2 : Mapper le CC#10 (panoramique) du canal 1 au panoramique de la piste 0**
        *   `> map 1 10 0 pan`

    *   **Exemple 3 : Mapper le CC#20 du canal 1 à un changement de programme sur la piste 1**
        *   `> map 1 20 1 program`
        *   La valeur du CC (0-127) changera directement le programme de la piste 1.

4.  **Lister les mappings actifs :**
    *   Pour voir tous les mappings que vous avez créés :
    *   `> listmaps`
    *   Exemple de sortie :
        ```
        Active MIDI Mappings:
          Ch:1 CC:7 -> Track 0 Volume
          Ch:1 CC:10 -> Track 0 Pan
          Ch:1 CC:20 -> Track 1 Program
        ```

5.  **Supprimer un mapping :**
    *   Si vous voulez supprimer un mapping :
    *   `> unmap <canal> <cc>`
    *   Exemple : `> unmap 1 10`

6.  **Arrêter l'écoute :**
    *   Pour que le séquenceur arrête d'écouter les messages de contrôle :
    *   `> unsetcontrolport`

---

### Utilisation programmatique (API)

Il est également possible d'utiliser les modules du séquenceur directement dans votre propre code Python.

```python
from sequencer.models import Song, Track, Event, Note
from sequencer.midi_export import export_to_midi

# ... votre code pour créer des objets Song, Track, etc. ...
```