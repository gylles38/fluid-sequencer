import unittest
from unittest.mock import MagicMock, patch, call
import mido
import time

# It's good practice to import the specific classes you're testing
from src.sequencer.sequencer import Sequencer
from src.sequencer.models import MidiTrack, Song

class TestSequencerRecording(unittest.TestCase):

    def setUp(self):
        """Set up a new Sequencer instance before each test."""
        self.sequencer = Sequencer()
        self.sequencer.song = Song(name="Test Song", tempo=120, time_signature_numerator=4)
        self.sequencer.add_track("Test MIDI", track_type='midi')
        # Assign a metronome port name, which is required for count-in
        self.sequencer.song.metronome_port_name = "metro_out"
        # We don't need real virtual ports for these tests, mocks are sufficient.

    def tearDown(self):
        """Clean up after each test."""
        # If any threads were started, ensure they are stopped.
        self.sequencer._stop_event.set()
        # Close any real ports if they were opened by mistake
        for port in self.sequencer.virtual_ports:
            port.close()

    @patch('src.sequencer.sequencer.time.sleep')
    @patch('src.sequencer.sequencer.mido.open_input')
    @patch('src.sequencer.sequencer.Sequencer.play')
    def test_record_waits_for_note(self, mock_play, mock_open_input, mock_sleep):
        """
        Verify that recording waits for a blocking MIDI note before starting.
        """
        # --- Setup Mocks ---
        mock_inport = MagicMock()
        # Simulate receiving a non-note message, then the note that starts recording
        mock_inport.receive.side_effect = [
            mido.Message('clock'),
            mido.Message('note_on', note=60, velocity=100)
        ]
        mock_open_input.return_value.__enter__.return_value = mock_inport

        # Stop the thread once play is called
        def stop_thread_on_play(*args, **kwargs):
            self.sequencer._stop_event.set()
        mock_play.side_effect = stop_thread_on_play

        # --- Call the method under test ---
        target_track = self.sequencer.song.tracks[0]
        self.sequencer._recording_thread_main(
            target_track=target_track,
            start_beat=0.0,
            inport_name="test_in",
            outport_name=None,
            num_beats_to_record=None,
            original_mute_state=False
        )

        # --- Assertions ---
        # 1. The system waited for a note using the blocking receive method.
        self.assertEqual(mock_inport.receive.call_count, 2)

        # 2. Recording started after the note was received.
        mock_play.assert_called_once_with(start_beat=0.0)
