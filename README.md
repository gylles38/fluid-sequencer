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
| `add <nom> [instr]`      | Ajoute une nouvelle piste vide. `instr` est un numéro d'instrument MIDI optionnel (0-127). |
| `load <fichier>`         | Charge un fichier MIDI (`.mid`) et l'ajoute comme une nouvelle piste.         |
| `list`                   | Affiche toutes les pistes de la chanson en cours, avec leurs détails.       |
| `record <index_piste>`   | Démarre l'enregistrement sur une piste. Utilisez `list` pour trouver l'`<index_piste>`. |
| `tempo <bpm>`            | Règle le tempo de la chanson en battements par minute.                      |
| `save <fichier>`         | Sauvegarde la chanson entière dans un nouveau fichier MIDI.                 |
| `play`                   | Joue la chanson actuelle depuis le début.                                   |
| `pause`                  | Met en pause ou reprend la lecture.                                         |
| `stop`                   | Arrête la lecture et réinitialise la position.                              |
| `quit`                   | Quitte le séquenceur.                                                       |

---

### Utilisation programmatique (API)

Il est également possible d'utiliser les modules du séquenceur directement dans votre propre code Python.

```python
from sequencer.models import Song, Track, Event, Note
from sequencer.midi_export import export_to_midi

# ... votre code pour créer des objets Song, Track, etc. ...
```
