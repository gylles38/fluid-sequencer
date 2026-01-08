import unittest
from unittest.mock import mock_open, patch
import json
from sequencer.sequencer import Sequencer
from sequencer.models import Song, MidiTrack
from sequencer.serialization import CustomSongEncoder, song_decoder

class TestSerialization(unittest.TestCase):

    def test_save_and_load_project_with_separated_serialization(self):
        """
        Test that a project can be saved and loaded correctly using the
        separated CustomSongEncoder and song_decoder.
        """
        # 1. Create a sequencer and populate it with some data
        sequencer = Sequencer()
        sequencer.set_tempo(133)
        sequencer.add_track(name="Test Track", track_type='midi', instrument=10)

        # 2. Save the project to a string using the CustomSongEncoder
        project_data = {
            "song": sequencer.song,
            "virtual_ports": [],
            "control_port_name": None,
            "audio_player_command": sequencer.DEFAULT_AUDIO_PLAYER_COMMAND
        }
        json_string = json.dumps(project_data, cls=CustomSongEncoder, indent=4)

        # 3. Load the project from the string using the song_decoder
        loaded_project_data = json.loads(json_string, object_hook=song_decoder)

        # 4. Create a new sequencer and manually populate it from the loaded data
        # (This mimics what sequencer.load_project does)
        new_sequencer = Sequencer()
        new_sequencer.song = loaded_project_data.get("song")

        # 5. Assert that the data was restored correctly
        self.assertEqual(new_sequencer.song.tempo, 133)
        self.assertEqual(len(new_sequencer.song.tracks), 1)
        self.assertIsInstance(new_sequencer.song.tracks[0], MidiTrack)
        self.assertEqual(new_sequencer.song.tracks[0].name, "Test Track")
        self.assertEqual(new_sequencer.song.tracks[0].instrument, 10)

if __name__ == '__main__':
    unittest.main()
