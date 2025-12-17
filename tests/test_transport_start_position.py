import unittest
from unittest.mock import MagicMock, patch
import os
import sys

# Ensure the 'src' directory is in the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from sequencer.sequencer import Sequencer

class TestTransportStartPosition(unittest.TestCase):

    @patch('sequencer.sequencer.JackManager')
    def test_play_uses_ui_start_pos_when_stopped(self, mock_jack_manager_class):
        """
        Verify that calling 'play' when stopped uses the 'ui_start_pos_str'
        property, ignoring the current playhead position.
        """
        # --- 1. Setup mocks and sequencer ---
        mock_jack_manager_instance = mock_jack_manager_class.return_value
        sequencer = Sequencer()

        # --- 2. Set up the scenario ---
        # Simulate that the sequencer is stopped
        sequencer.playback_state = "stopped"

        # Simulate a current playhead position (e.g., where it was last stopped)
        mock_jack_manager_instance.get_current_beat.return_value = 16.0  # Beat of measure 5:1

        # Set the desired start position in the UI field
        sequencer.ui_start_pos_str = "2:1" # Should correspond to beat 4.0

        # --- 3. Trigger the play command ---
        sequencer.process_transport_command("play_pause")

        # --- 4. Assert that the play method was called with the correct start beat ---
        # The `play` method is the final step in the chain. We verify it received
        # the beat calculated from `ui_start_pos_str`, not the beat from `get_current_beat`.
        sequencer.play.assert_called_once()
        call_args, call_kwargs = sequencer.play.call_args

        expected_start_beat = 4.0  # Beat corresponding to "2:1" in a 4/4 signature
        self.assertIn('start_beat', call_kwargs)
        self.assertAlmostEqual(call_kwargs['start_beat'], expected_start_beat)

    def setUp(self):
        """Patch the 'play' method of the Sequencer to intercept its call."""
        # This patch needs to be active for the duration of the test.
        # We replace `sequencer.play` with a mock to check what it's called with.
        self.play_patcher = patch.object(Sequencer, 'play', autospec=True)
        self.mock_play = self.play_patcher.start()

        # We also need to prevent the original __init__ from running `AudioSegment.from_file`
        self.pydub_patcher = patch('sequencer.sequencer.AudioSegment.from_file')
        self.mock_from_file = self.pydub_patcher.start()
        self.mock_from_file.return_value = MagicMock(__len__=1000)

    def tearDown(self):
        """Stop the patchers."""
        self.play_patcher.stop()
        self.pydub_patcher.stop()

if __name__ == '__main__':
    unittest.main()
