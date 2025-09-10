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

    @patch('src.sequencer.sequencer.time')
    @patch('src.sequencer.sequencer.mido.open_input')
    @patch('src.sequencer.sequencer.open_output')
    @patch('src.sequencer.sequencer.Sequencer.play')
    def test_record_with_count_in_plays_metronome_then_records_automatically(self, mock_play, mock_open_output, mock_open_input, mock_time_module):
        """
        Verify that when a count-in is enabled, the metronome plays for the
        correct duration and then recording starts automatically without
        waiting for a note.
        """
        # --- Setup Mocks ---
        # Mock time.time and time.sleep to control time flow in the test
        current_time = 100.0
        def time_sleep_mock(seconds):
            nonlocal current_time
            current_time += seconds
        def time_time_mock():
            return current_time
        mock_time_module.time = time_time_mock
        mock_time_module.sleep = time_sleep_mock

        # Mock the MIDI input and output ports
        mock_inport = MagicMock()
        mock_metro_port = MagicMock()
        mock_open_input.return_value.__enter__.return_value = mock_inport
        mock_open_output.return_value = mock_metro_port

        # The recording loop is infinite, so we need a way to stop it.
        # We'll set the stop event when iter_pending is called the first time.
        def stop_on_iter_pending(*args, **kwargs):
            self.sequencer._stop_event.set()
            return [] # iter_pending must return an iterable
        mock_inport.iter_pending.side_effect = stop_on_iter_pending

        # --- Call the method under test ---
        target_track = self.sequencer.song.tracks[0]
        # We run the recording logic in the main thread for testing
        self.sequencer._recording_thread_main(
            target_track=target_track,
            start_beat=0.0,
            inport_name="test_in",
            outport_name=None,
            num_beats_to_record=None,
            original_mute_state=False,
            count_in_measures=1  # Test a 1-measure count-in
        )

        # --- Assertions ---
        # 1. Metronome port was opened
        mock_open_output.assert_called_with("metro_out")

        # 2. Metronome sent the correct number of clicks (4 beats * 2 messages each)
        self.assertEqual(mock_metro_port.send.call_count, 8)

        # 3. The pitches of the clicks were correct (downbeat is different)
        downbeat_pitch = self.sequencer.metronome_pitch_downbeat
        beat_pitch = self.sequencer.metronome_pitch_beat
        sent_note_ons = [c.args[0] for c in mock_metro_port.send.call_args_list if c.args[0].type == 'note_on']
        self.assertEqual(sent_note_ons[0].note, downbeat_pitch)
        self.assertEqual(sent_note_ons[1].note, beat_pitch)
        self.assertEqual(sent_note_ons[2].note, beat_pitch)
        self.assertEqual(sent_note_ons[3].note, beat_pitch)

        # 4. The timing is correct. Tempo is 120bpm -> 0.5s per beat. 1 measure is 2s.
        # The internal logic has a 0.05s sleep for the note sound itself.
        expected_time_after_count_in = 100.0 + (4 * 0.5)
        self.assertAlmostEqual(current_time, expected_time_after_count_in, delta=0.01)

        # 5. Recording (and playback of other tracks) started after the count-in.
        mock_play.assert_called_once_with(start_beat=0.0)

        # 6. The system did NOT wait for a blocking note read.
        mock_inport.receive.assert_not_called()
        # It should, however, have polled for notes.
        mock_inport.iter_pending.assert_called()

    @patch('src.sequencer.sequencer.time.sleep')
    @patch('src.sequencer.sequencer.mido.open_input')
    @patch('src.sequencer.sequencer.Sequencer.play')
    def test_record_without_count_in_waits_for_note(self, mock_play, mock_open_input, mock_sleep):
        """
        Verify that when count-in is disabled, the system waits for a
        blocking MIDI note before starting the recording.
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
            original_mute_state=False,
            count_in_measures=0  # No count-in
        )

        # --- Assertions ---
        # 1. The system waited for a note using the blocking receive method.
        self.assertEqual(mock_inport.receive.call_count, 2)

        # 2. Recording started after the note was received.
        mock_play.assert_called_once_with(start_beat=0.0)
