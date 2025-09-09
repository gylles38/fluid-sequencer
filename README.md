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

3.  **Installez le projet** :
    Cette commande installe le projet et ses dépendances (comme `mido`).
    ```bash
    pip install -e .
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
  copy                    - Copies a section of a track using 'measure:beat' positions.
  move <track_index>      - Moves a section of a track using 'measure:beat' positions.
  transpose               - Transposes a section of a track using 'measure:beat' positions.
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
```

---

## Sauvegarde et Chargement de Projets

Pour éviter de reconfigurer vos ports virtuels et vos assignations de pistes à chaque session, vous pouvez utiliser les commandes de projet.

-   **`saveproject <nom>`** : Cette commande sauvegarde deux fichiers :
    1.  `<nom>.mid` : Le fichier MIDI standard contenant toutes vos notes.
    2.  `<nom>.proj.json` : Un fichier de configuration qui mémorise les ports virtuels que vous avez créés, les assignations de pistes, le chemin des pistes audio et d'autres réglages.

-   **`loadproject <nom>`** : Cette commande charge un projet complet. Elle va :
    1.  Lire le fichier `<nom>.proj.json`.
    2.  Charger le fichier MIDI associé.
    3.  Recréer automatiquement les ports virtuels.
    4.  Réassigner les pistes aux bons ports.

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
    *   Activer le "MIDI Thru" (`y`) et choisir `mon-synth` comme port de sortie.
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
    *   Le volume des pistes audio peut être contrôlé (si le lecteur externe le supporte via son volume système).
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

### How-To : Créer une courbe d'automation de volume

L'automation permet de faire évoluer un paramètre (comme le volume, le pan, etc.) au fil du temps. Voici comment créer un fondu de volume (fade-in) sur une piste.

1.  **Créez vos pistes :**
    *   `> add piano` (crée la piste MIDI 0)
    *   `> addauto "Piano Volume" 0` (crée une piste d'automation qui cible la piste 0)

2.  **Affichez la liste des pistes** pour vérifier : `list`
    ```
    [0] piano (MIDI) ...
    [1] Piano Volume (Automation) (Target: 0 'piano', 0 points)
    ```

3.  **Créez les points d'automation (Exemple : fondu linéaire) :**
    *   Nous allons créer un fondu qui commence à la mesure 1 et se termine à la mesure 3.
    *   **Point de départ :** volume à 0 au début de la mesure 1. On utilise une courbe `linear` pour indiquer que la valeur doit progresser vers le point suivant.
        *   `> addap 1 1:1 vol 0.0 linear`
    *   **Point d'arrivée :** volume à 1 (maximum) au début de la mesure 3. La courbe `none` est utilisée ici car c'est la fin de notre rampe (la valeur restera à 1.0 après ce point).
        *   `> addap 1 3:1 vol 1.0 none`

4.  **Explorez d'autres courbes :**
    *   Pour un fondu qui commence lentement et accélère (`ease-in`):
        *   `> addap 1 1:1 vol 0.0 ease-in`
        *   `> addap 1 3:1 vol 1.0 none`
    *   Pour un fondu qui commence vite et ralentit (`ease-out`):
        *   `> addap 1 1:1 vol 0.0 ease-out`
        *   `> addap 1 3:1 vol 1.0 none`
    *   Pour un fondu en forme de S (`ease-in-out` ou `sine`):
        *   `> addap 1 1:1 vol 0.0 ease-in-out`
        *   `> addap 1 3:1 vol 1.0 none`

5.  **Jouez la piste :**
    *   `> play`
    *   Si vous avez des notes sur la piste "piano" entre les mesures 1 et 3, vous entendrez le volume augmenter progressivement. Le séquenceur calcule automatiquement toutes les étapes intermédiaires (via des messages MIDI CC) pour créer une rampe fluide selon la courbe choisie.

---

### Utilisation programmatique (API)

Il est également possible d'utiliser les modules du séquenceur directement dans votre propre code Python.

```python
from sequencer.models import Song, Track, Event, Note
from sequencer.midi_export import export_to_midi

# ... votre code pour créer des objets Song, Track, etc. ...
```
