import sys
import os

# Ensure the 'src' directory is in the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'src')))

from sequencer.sequencer import Sequencer
from sequencer.models import Note, Event, MidiTrack

def create_test_project():
    """Creates a standard project file for manual testing."""
    print("Creating a test project file: test_project.proj.json")

    seq = Sequencer()

    # Audio track starts at beat 0 (1:1)
    # Using the Sheep-bass.ogg file as it's known to exist.
    seq.add_track(name="drums", track_type="audio", filepath="Sheep-bass.ogg")

    # MIDI track
    seq.add_track(name="bass", track_type="midi", instrument=33)

    # Find the bass track to add notes to it
    bass_track = next((t for t in seq.song.tracks if isinstance(t, MidiTrack)), None)

    if bass_track:
        # Add notes for 4 measures
        for i in range(8):
            beat = i * 2.0
            pitch = 40 if i % 2 == 0 else 42
            bass_track.add_event(Event(notes=[Note(pitch=pitch, duration=1.0)], start_time=beat))
    else:
        print("Could not find the MIDI track to add notes.")
        return

    # Assign the MIDI track to a virtual port
    seq.create_virtual_port("test_vport")
    # Find the index of the bass track to assign it
    bass_track_index = seq.song.tracks.index(bass_track)
    seq.assign_port(bass_track_index, "test_vport")

    seq.save_project("test_project")
    print("Test project 'test_project.proj.json' created successfully.")
    print("You can now run 'python3 main.py' and use this project for testing.")

if __name__ == "__main__":
    create_test_project()
