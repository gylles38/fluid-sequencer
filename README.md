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
| `ports`                  | Liste les ports d'entrée et de sortie MIDI disponibles.                     |
| `vport <nom>`            | Crée un port de sortie MIDI virtuel pour l'utiliser avec d'autres applications. |
| `assign <piste> <port>`  | Assigne une piste à un port de sortie MIDI pour la lecture.                 |
| `record <piste>`         | Enregistre le MIDI sur une piste, avec une option de "MIDI thru" en direct.  |
| `delete <piste>`         | Supprime une piste après confirmation.                                      |
| `tempo <bpm>`            | Règle le tempo de la chanson en battements par minute.                      |
| `save <fichier>`         | Sauvegarde la chanson entière dans un nouveau fichier MIDI.                 |
| `play`                   | Joue la chanson actuelle depuis le début.                                   |
| `pause`                  | Met en pause ou reprend la lecture.                                         |
| `stop`                   | Arrête la lecture et réinitialise la position.                              |
| `quit`                   | Quitte le séquenceur.                                                       |

---

## Utilisation avec Carla

Pour connecter ce séquenceur avec un hôte de plugins comme Carla, vous pouvez utiliser la commande `vport` pour créer un "câble MIDI virtuel".

### Cas 1 : Jouer une piste du séquenceur avec un instrument dans Carla

1.  **Lancez le séquenceur** et **créez un port virtuel** :
    ```sh
    > vport carla-in
    ```
2.  **Lancez Carla.** Dans l'onglet "Patchbay", vous devriez voir `carla-in` comme une source MIDI.
3.  **Chargez un instrument** dans Carla.
4.  **Connectez le port :** Dans Carla, reliez la sortie `carla-in` à l'entrée MIDI de votre instrument.
5.  **Dans le séquenceur, assignez votre piste** à ce nouveau port. Utilisez `ports` pour voir son index, puis `assign <index_piste> <index_port>`.
    ```sh
    > assign 0 0  # Assigne la piste 0 au port 0 (carla-in)
    ```
6.  **Jouez !**
    ```sh
    > play
    ```
    La piste sera maintenant jouée par l'instrument dans Carla.

### Cas 2 : S'entendre en direct pendant l'enregistrement (MIDI Thru)

1.  Suivez les étapes 1 à 4 ci-dessus pour connecter le port `carla-in` à un instrument.
2.  Dans le séquenceur, lancez l'enregistrement :
    ```sh
    > record 0
    ```
3.  Choisissez votre clavier physique comme **port d'entrée**.
4.  À la question "Enable MIDI Thru...", répondez `y`.
5.  Choisissez le port `carla-in` comme **port de sortie**.
6.  Vous pouvez maintenant jouer sur votre clavier et vous entendrez le son de l'instrument de Carla en temps réel pendant que le séquenceur enregistre.

---

### Utilisation programmatique (API)

Il est également possible d'utiliser les modules du séquenceur directement dans votre propre code Python.

```python
from sequencer.models import Song, Track, Event, Note
from sequencer.midi_export import export_to_midi

# ... votre code pour créer des objets Song, Track, etc. ...
```
