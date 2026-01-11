import unittest
from unittest.mock import MagicMock, patch
import os
import sys

# Ensure the 'src' directory is in the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from sequencer.sequencer import Sequencer
from sequencer.models import AudioTrack

class TestDynamicTrackAdd(unittest.TestCase):

    @patch('sequencer.sequencer.AudioSegment.from_file')
    @patch('sequencer.sequencer.JackManager')
    def test_add_audio_track_while_running_launches_player(self, mock_jack_manager_class, mock_audio_from_file):
        """
        Verify that adding an audio track while the JackManager is running
        triggers the launch_player_for_track method.
        """
        # --- 1. Setup the mocks and sequencer ---
        # Mock pydub to prevent file reading errors
        mock_audio_from_file.return_value = MagicMock(__len__=MagicMock(return_value=1000))

        # Get the mock instance that will be used by the Sequencer
        mock_jack_manager_instance = mock_jack_manager_class.return_value

        # Simulate a running JackManager
        mock_jack_manager_instance.is_running = True

        # Instantiate the Sequencer *while the patch is active*
        sequencer = Sequencer()

        # --- 2. Add a new audio track ---
        dummy_filepath = "dummy.wav"
        track_name = "Dynamic Drums"

        sequencer.add_track(name=track_name, track_type="audio", filepath=dummy_filepath)

        # --- 3. Assert that the launch method was called ---
        mock_jack_manager_instance.launch_player_for_track.assert_called_once()

        # --- 4. Verify the arguments of the call ---
        call_args, call_kwargs = mock_jack_manager_instance.launch_player_for_track.call_args

        # The first argument should be the AudioTrack object
        added_track_object = call_args[0]
        self.assertIsInstance(added_track_object, AudioTrack)
        self.assertEqual(added_track_object.name, track_name)
        self.assertEqual(added_track_object.filepath, dummy_filepath)

        # The second argument should be the index of the newly added track
        expected_track_index = len(sequencer.song.tracks) - 1
        self.assertEqual(call_args[1], expected_track_index)

    @patch('sequencer.sequencer.AudioSegment.from_file')
    @patch('sequencer.sequencer.JackManager')
    def test_add_audio_track_while_stopped_does_not_launch_player(self, mock_jack_manager_class, mock_audio_from_file):
        """
        Verify that adding an audio track while the JackManager is stopped
        does NOT trigger the launch_player_for_track method.
        """
        # --- 1. Setup the mocks and sequencer ---
        mock_audio_from_file.return_value = MagicMock(__len__=MagicMock(return_value=1000))
        mock_jack_manager_instance = mock_jack_manager_class.return_value

        # Ensure JackManager is in a stopped state
        mock_jack_manager_instance.is_running = False

        sequencer = Sequencer()

        # --- 2. Add a new audio track ---
        sequencer.add_track(name="Test", track_type="audio", filepath="dummy.wav")

        # --- 3. Assert that the launch method was NOT called ---
        mock_jack_manager_instance.launch_player_for_track.assert_not_called()

if __name__ == '__main__':
    unittest.main()
