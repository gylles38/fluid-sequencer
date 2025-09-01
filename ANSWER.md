Bonjour,

C'est une excellente question, et je comprends parfaitement votre confusion. Vous avez raison, le comportement de la sauvegarde a changé, et j'aurais dû mieux communiquer ce changement.

**Ancien Comportement**
Auparavant, la commande `saveproject` sauvegardait deux fichiers :
1.  Un fichier MIDI standard (`.mid`).
2.  Un fichier de métadonnées (`.proj.json`).

**Nouveau Comportement (et pourquoi j'ai fait ce changement)**
Avec l'ajout des pistes audio, l'ancien système n'était plus possible. Un fichier MIDI standard (`.mid`) ne peut pas contenir de pistes audio.

Pour pouvoir sauvegarder un projet qui contient **à la fois** des pistes MIDI et des pistes audio, j'ai dû modifier la logique de `saveproject`.

Maintenant, `saveproject` crée un seul fichier, le `.proj.json`, qui contient **toutes** les informations sur votre projet :
-   Les réglages généraux (tempo, etc.).
-   La liste de toutes les pistes.
-   Pour les pistes MIDI, toutes les notes et les événements sont stockés directement dans ce fichier.
-   Pour les pistes audio, le chemin vers le fichier audio est stocké.

**En résumé : Le fichier `.proj.json` est maintenant votre fichier de projet complet.**

**Si vous voulez toujours un fichier MIDI standard :**
Vous pouvez toujours utiliser la commande `save <nom_du_fichier>.mid`. Cette commande exportera **uniquement** les pistes MIDI de votre projet dans un fichier `.mid` standard, en ignorant les pistes audio.

J'espère que cette explication clarifie les choses.

Cordialement,
Jules

---
**Mise à jour concernant les fichiers OGG :**

J'ai installé l'outil système `ffmpeg`. Avec cet ajout, la librairie audio `pydub` que nous utilisons devrait maintenant être capable de charger et de lire les fichiers au format `.ogg` sans problème. Vous pouvez les ajouter avec la commande `addaudio <nom> <chemin/vers/fichier.ogg>`.

Malheureusement, à cause de problèmes persistants avec l'environnement de test, je n'ai pas pu vérifier cela avec un test automatisé. Cependant, la configuration requise est en place.

---
**Mise à jour concernant la lecture des fichiers audio :**

Vous avez signalé que la lecture s'arrêtait prématurément lors de l'utilisation de pistes audio. C'était un excellent retour. J'ai identifié et corrigé un bug dans le moteur de lecture. Le programme ne attendait pas la fin de la lecture des fichiers audio avant de terminer la séquence.

Ce problème est maintenant résolu. La lecture devrait se poursuivre jusqu'à ce que tous les clips audio soient terminés.
