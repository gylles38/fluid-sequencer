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
| `load <fichier>`         | Charge un fichier MIDI (`.mid`) et l'ajoute comme une nouvelle piste.         |
| `list`                   | Affiche toutes les pistes de la chanson en cours, avec leurs détails.       |
| `ports`                  | Liste les ports d'entrée et de sortie MIDI disponibles.                     |
| `vport <nom>`            | Crée un port de sortie MIDI virtuel pour l'utiliser avec d'autres applications. |
| `assign <piste>`         | Assigne une piste à un port de sortie à partir d'une liste de choix.        |
| `setbank <piste> <msb> [lsb]` | Définit la banque MIDI pour une piste (MSB=CC0, LSB=CC32).                  |
| `setch <piste> <canal>`  | Définit le canal MIDI (1-16) pour une piste.                                |
| `record <piste>`         | Enregistre le MIDI sur une piste, avec une option de "MIDI thru" en direct.  |
| `delete <piste>`         | Supprime une piste après confirmation.                                      |
| `tempo <bpm>`            | Règle le tempo de la chanson en battements par minute.                      |
| `save <fichier>`         | Sauvegarde la chanson entière dans un nouveau fichier MIDI.                 |
| `play`                   | Joue la chanson actuelle depuis le début.                                   |
| `pause`                  | Met en pause ou reprend la lecture.                                         |
| `stop`                   | Arrête la lecture et réinitialise la position.                              |
| `quit`                   | Quitte le séquenceur.                                                       |

---

## Exemples d'utilisation

### How-To : Enregistrer deux pistes avec des canaux et banques différents

Voici un exemple de workflow pour enregistrer deux pistes, chacune avec un canal, un instrument et une banque de sons spécifiques.

1.  **Ajouter et configurer la Piste 1 :**
    *   Ajoutez une piste pour un piano (programme 1). Par défaut, elle sera sur le canal 1.
        `> add piano 1`
    *   Définissez la banque de sons. Par exemple, pour la banque 1, programme 1 :
        `> setbank 0 1 1` (piste 0, MSB 1, LSB 1)

2.  **Ajouter et configurer la Piste 2 :**
    *   Ajoutez une piste pour des cordes (programme 92). Par défaut, elle sera sur le canal 2.
        `> add cordes 92`
    *   Définissez la banque de sons. Par exemple, pour la banque 2, programme 92 :
        `> setbank 1 2 92` (piste 1, MSB 2, LSB 92)

3.  **(Optionnel) Changer le canal d'une piste :** Si votre instrument externe pour les cordes écoute sur le canal 10, par exemple :
    `> setch 1 10` (change le canal de la piste 1 pour le canal 10)

4.  **Lister les pistes** pour vérifier la configuration : `list`
    ```
    [0] piano (Ch: 1, Prog: 1, Bank: 1:1, 0 events)
    [1] cordes (Ch: 10, Prog: 92, Bank: 2:92, 0 events)
    ```

5.  **Enregistrer et jouer les pistes** comme d'habitude. Le séquenceur utilisera les canaux et les banques que vous avez spécifiés.

---

## Utilisation avec Carla

Pour connecter ce séquenceur avec un hôte de plugins comme Carla, vous pouvez utiliser la commande `vport` pour créer un "câble MIDI virtuel".

### Cas 1 : Jouer une piste du séquenceur avec un instrument dans Carla

1.  **Lancez le séquenceur** et **créez un port virtuel** : `> vport carla-in`
2.  **Lancez Carla.** Dans l'onglet "Patchbay", vous devriez voir `carla-in` comme une source MIDI (il faut peut-être utiliser `a2jmidid` et bien configurer Carla).
3.  **Chargez un instrument** dans Carla. Assurez-vous que son canal d'entrée MIDI correspond à celui de la piste dans le séquenceur.
4.  **Connectez le port :** Dans Carla, reliez la sortie `carla-in` à l'entrée MIDI de votre instrument.
5.  **Dans le séquenceur, assignez votre piste** à ce nouveau port : `assign 0` -> choisissez `carla-in` dans la liste.
6.  **Jouez !** `play`

### Cas 2 : S'entendre en direct pendant l'enregistrement (MIDI Thru)

1.  Suivez les étapes 1 à 4 ci-dessus pour connecter le port `carla-in` à un instrument.
2.  Dans le séquenceur, lancez l'enregistrement : `record 0`
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
