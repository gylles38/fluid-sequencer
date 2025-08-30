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

### Commandes disponibles

| Commande                 | Description                                                                 |
| ------------------------ | --------------------------------------------------------------------------- |
| `help`                   | Affiche le message d'aide.                                                  |
| `add <nom> [prog]`       | Ajoute une nouvelle piste vide. `prog` est le numéro de programme MIDI (0-127). |
| `load <fichier>`         | Charge uniquement un fichier MIDI.                                          |
| `loadproject <basename>` | Charge un projet complet (`.mid` et `.proj.json`).                          |
| `list`                   | Affiche toutes les pistes de la chanson en cours, avec leurs détails.       |
| `ports`                  | Liste les ports d'entrée et de sortie MIDI disponibles.                     |
| `vport <nom>`            | Crée un port de sortie MIDI virtuel pour l'utiliser avec d'autres applications. |
| `assign <piste>`         | Assigne une piste à un port de sortie à partir d'une liste de choix.        |
| `setbank <piste> <msb> [lsb]` | Définit la banque MIDI pour une piste (MSB=CC0, LSB=CC32).                  |
| `setch <piste> <canal>`  | Définit le canal MIDI (1-16) pour une piste.                                |
| `setprog <piste> <prog>` | Définit le programme MIDI (0-127) pour une piste.                           |
| `record <piste>`         | Enregistre le MIDI sur une piste, avec une option de "MIDI thru" en direct.  |
| `delete <piste>`         | Supprime une piste après confirmation.                                      |
| `tempo <bpm>`            | Règle le tempo de la chanson en battements par minute.                      |
| `save <fichier>`         | Sauvegarde uniquement la chanson dans un fichier MIDI.                      |
| `saveproject <basename>` | Sauvegarde le projet complet (MIDI et configuration).                       |
| `play`                   | Joue la chanson actuelle depuis le début.                                   |
| `pause`                  | Met en pause ou reprend la lecture.                                         |
| `stop`                   | Arrête la lecture et réinitialise la position.                              |
| `quit`                   | Quitte le séquenceur.                                                       |

---

## Sauvegarde et Chargement de Projets

Pour éviter de reconfigurer vos ports virtuels et vos assignations de pistes à chaque session, vous pouvez utiliser les commandes de projet.

-   **`saveproject <nom>`** : Cette commande sauvegarde deux fichiers :
    1.  `<nom>.mid` : Le fichier MIDI standard contenant toutes vos notes.
    2.  `<nom>.proj.json` : Un fichier de configuration qui mémorise les ports virtuels que vous avez créés et quelles pistes leur sont assignées.

-   **`loadproject <nom>`** : Cette commande charge un projet complet. Elle va :
    1.  Lire le fichier `<nom>.proj.json`.
    2.  Charger le fichier MIDI associé.
    3.  Recréer automatiquement les ports virtuels.
    4.  Réassigner les pistes aux bons ports.

---

## Exemples d'utilisation

### How-To : Configurer et enregistrer une piste

Voici un exemple de workflow complet.

1.  **Ajouter une piste :**
    *   `> add piano` (ajoute une piste nommée "piano" avec le programme 0 par défaut)

2.  **Configurer l'instrument :**
    *   Changer le programme pour un piano électrique (ex: 5) : `> setprog 0 5`
    *   Changer le canal MIDI pour le canal 10 : `> setch 0 10`
    *   Définir la banque de sons (ex: MSB=1, LSB=1) : `> setbank 0 1 1`

3.  **Lister les pistes** pour vérifier la configuration : `list`
    ```
    [0] piano (Ch: 10, Prog: 5, Bank: 1:1, 0 events)
    ```

4.  **Connecter à un synthétiseur (via Carla) :**
    *   Créer un port virtuel : `> vport mon-synth`
    *   Lancer Carla, y charger un synthétiseur, et le configurer pour qu'il écoute sur le **canal 10**.
    *   Dans la baie de patch de Carla, connecter la sortie `mon-synth` à l'entrée du synthétiseur.
    *   Assigner la piste au port virtuel : `> assign 0` -> choisir `mon-synth` dans la liste.

5.  **Enregistrer la piste en s'écoutant en direct :**
    *   Lancer l'enregistrement : `> record 0`
    *   Choisir votre clavier physique comme port d'entrée.
    *   Activer le "MIDI Thru" (`y`) et choisir `mon-synth` comme port de sortie.
    *   Jouez ! Vous entendrez le son du synthé de Carla pendant l'enregistrement.

6.  **Sauvegarder le projet :**
    *   `> saveproject mon_morceau`

7.  Plus tard, vous pourrez tout recharger avec `loadproject mon_morceau`.


---

### Utilisation programmatique (API)

Il est également possible d'utiliser les modules du séquenceur directement dans votre propre code Python.

```python
from sequencer.models import Song, Track, Event, Note
from sequencer.midi_export import export_to_midi

# ... votre code pour créer des objets Song, Track, etc. ...
```
