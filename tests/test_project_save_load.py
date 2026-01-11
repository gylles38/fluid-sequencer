import unittest
import os
import sys
import tempfile
import json

# Ensure the 'src' directory is in the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from sequencer.sequencer import Sequencer
from sequencer.models import MidiTrack, AudioTrack, Note, Event

class TestProjectSaveLoad(unittest.TestCase):

    def setUp(self):
        """Set up a temporary directory for test files."""
        self.test_dir = tempfile.TemporaryDirectory()
        self.sequencer = Sequencer()

    def tearDown(self):
        """Clean up the temporary directory."""
        self.test_dir.cleanup()

    def test_save_and_load_project_preserves_data(self):
        """
        Verify that saving a project and then loading it restores all
        track data, including MIDI notes and audio track properties.
        """
        # --- 1. Create a project with various track types and data ---
        seq = self.sequencer

        # Add a MIDI track with a note
        midi_note = Note(pitch=60, velocity=100, duration=1.0)
        midi_event = Event(start_time=0.0, notes=[midi_note])
        midi_track = MidiTrack(
            name="Piano",
            instrument=1,
            volume=0.7,
            pan=-0.5,
            events=[midi_event],
            output_port_name="VirtualPort1"
        )
        seq.song.add_track(midi_track)

        # Create a dummy audio file for the audio track
        dummy_audio_path = os.path.join(self.test_dir.name, "test_audio.wav")
        with open(dummy_audio_path, "w") as f:
            f.write("dummy audio data")

        # Add an audio track
        audio_track = AudioTrack(
            name="Drums",
            filepath=dummy_audio_path,
            volume=0.9,
            pan=0.25,
            start_time=4.0
        )
        seq.song.add_track(audio_track)

        project_basename = os.path.join(self.test_dir.name, "test_project")

        # --- 2. Save the project ---
        save_result = seq.save_project(project_basename)
        self.assertIn("Project saved", save_result)

        project_filepath = f"{project_basename}.proj.json"
        self.assertTrue(os.path.exists(project_filepath))

        # --- 3. Create a new sequencer instance and load the project ---
        new_seq = Sequencer()
        load_result = new_seq.load_project(project_basename)
        self.assertIn("Successfully loaded", load_result)

        # --- 4. Assert that all data has been restored correctly ---
        self.assertEqual(len(new_seq.song.tracks), 2, "Incorrect number of tracks loaded.")

        # Verify MIDI track
        loaded_midi_track = new_seq.song.tracks[0]
        self.assertIsInstance(loaded_midi_track, MidiTrack, "First track is not a MidiTrack.")
        self.assertEqual(loaded_midi_track.name, "Piano")
        self.assertEqual(loaded_midi_track.instrument, 1)
        self.assertAlmostEqual(loaded_midi_track.volume, 0.7)
        self.assertAlmostEqual(loaded_midi_track.pan, -0.5)
        self.assertEqual(loaded_midi_track.output_port_name, "VirtualPort1")

        self.assertEqual(len(loaded_midi_track.events), 1, "MIDI track should have one event.")
        loaded_event = loaded_midi_track.events[0]
        self.assertEqual(len(loaded_event.notes), 1, "Event should have one note.")
        loaded_note = loaded_event.notes[0]
        self.assertEqual(loaded_note.pitch, 60)
        self.assertEqual(loaded_note.velocity, 100)
        self.assertEqual(loaded_note.duration, 1.0)

        # Verify Audio track
        loaded_audio_track = new_seq.song.tracks[1]
        self.assertIsInstance(loaded_audio_track, AudioTrack, "Second track is not an AudioTrack.")
        self.assertEqual(loaded_audio_track.name, "Drums")
        self.assertEqual(loaded_audio_track.filepath, dummy_audio_path)
        self.assertAlmostEqual(loaded_audio_track.volume, 0.9)
        self.assertAlmostEqual(loaded_audio_track.pan, 0.25)
        self.assertEqual(loaded_audio_track.start_time, 4.0)


if __name__ == '__main__':
    unittest.main()
