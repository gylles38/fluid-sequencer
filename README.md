# Sequencer minimaliste audio/midi en python

This project is a minimalist audio/MIDI sequencer in Python.

## Installation (Linux)

1.  **Créez un environnement virtuel**

    ```bash
    python3 -m venv venv
    ```

2.  **Activez l'environnement virtuel**

    ```bash
    source venv/bin/activate
    ```

3.  **Installez les dépendances**

    ```bash
    pip install -r requirements.txt
    ```

## How to use

Here is an example of how to create a simple song and export it to a MIDI file.
Save this code as `create_simple_song.py` and run it with `python create_simple_song.py`.

```python
import sys
import os

# Add the 'src' directory to the Python path to find the 'sequencer' module.
# This is not needed if the package is installed, but is useful for development.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from sequencer.models import Song, Track, Event, Note
from sequencer.midi_export import export_to_midi

def main():
    """Creates a simple song and exports it to a MIDI file."""
    # 1. Create a song
    song = Song(name="Simple Song", tempo=120)

    # 2. Create a track
    piano_track = Track(name="Piano Melody", instrument=0) # 0 is Acoustic Grand Piano

    # 3. Add notes to the track (a C-major scale)
    c_major_scale = [60, 62, 64, 65, 67, 69, 71, 72] # MIDI note numbers for C4 to C5
    for i, pitch in enumerate(c_major_scale):
        note = Note(pitch=pitch, velocity=100, duration=0.5)
        event = Event(notes=[note], start_time=i * 0.5)
        piano_track.add_event(event)

    # 4. Add the track to the song
    song.add_track(piano_track)

    # 5. Export the song to a MIDI file
    output_filename = "simple_song.mid"
    export_to_midi(song, output_filename)

    print(f"Song '{song.name}' has been successfully exported to '{output_filename}'")

if __name__ == "__main__":
    main()
```
