import unittest
from unittest.mock import patch, MagicMock
import mido
import time
import threading
from sequencer.sequencer import Sequencer
from sequencer.models import MidiMapping, MidiTrack, Song

class TestUnifiedListener(unittest.TestCase):
    def setUp(self):
        # Mock Clock to execute immediately
        self.clock_patcher = patch('sequencer.sequencer.Clock.schedule_once')
        self.mock_clock = self.clock_patcher.start()
        self.mock_clock.side_effect = lambda f, *args, **kwargs: f(0.1) # Call immediately with dummy dt

        self.seq = Sequencer()
        self.seq.song = Song(name="Test Song")
        self.seq.add_track("Track 1", track_type='midi')

    def tearDown(self):
        self.clock_patcher.stop()
        if self.seq._unified_midi_listener_thread:
            self.seq._unified_midi_listener_stop_event.set()
            self.seq._unified_midi_listener_thread.join(timeout=1.0)

    def _setup_mock_port(self, mock_open_input, messages_to_yield):
        mock_port = MagicMock()
        mock_open_input.return_value = mock_port

        # Generator for side_effect
        def iter_gen():
            yield [] # clear pending at start of loop
            for msgs in messages_to_yield:
                yield msgs
            while True:
                yield []

        mock_port.__enter__.return_value.iter_pending.side_effect = iter_gen()
        return mock_port

    @patch('sequencer.sequencer.get_input_names', return_value=["TestPort"])
    @patch('mido.open_input')
    def test_transport_via_unified_listener(self, mock_open_input, mock_get_names):
        msg = mido.Message('control_change', control=118, value=127)
        self._setup_mock_port(mock_open_input, [[msg]])

        with patch.object(self.seq, 'process_transport_command') as mock_process:
            self.seq.set_default_record_port("TestPort")
            time.sleep(0.5)
            mock_process.assert_called_with("play_pause")

    @patch('sequencer.sequencer.get_input_names', return_value=["TestPort"])
    @patch('mido.open_input')
    def test_recording_triggered_by_note(self, mock_open_input, mock_get_names):
        # Trigger with Note On, then send Note Off to push to merge queue
        msg_on = mido.Message('note_on', note=60, velocity=100)
        msg_off = mido.Message('note_off', note=60, velocity=0)
        self._setup_mock_port(mock_open_input, [[msg_on], [msg_off]])

        # Arm recording
        self.seq.last_record_settings = {'start_beat': 0.0}
        self.seq.is_recording = True
        self.seq.playback_state = 'stopped'

        # Mock current beat to increase
        with patch.object(self.seq, '_get_current_beat', side_effect=[0.0, 1.0, 1.0, 1.0]):
            with patch.object(self.seq.jack_manager, '_get_input_routing_value', return_value=0):
                with patch.object(self.seq, 'play') as mock_play:
                    def mock_play_side_effect(start_beat=0.0):
                        self.seq.playback_state = 'playing'
                    mock_play.side_effect = mock_play_side_effect

                    self.seq.set_default_record_port("TestPort")
                    time.sleep(0.6)

                    mock_play.assert_called()

                    # Check if note was added to merge queue after Note Off
                    self.assertTrue(len(self.seq.jack_manager._recorded_events_to_merge) > 0)
                    event = self.seq.jack_manager._recorded_events_to_merge[0]
                    self.assertEqual(event['type'], 'note')
                    self.assertEqual(event['pitch'], 60)
                    self.assertEqual(event['duration'], 1.0)

    @patch('sequencer.sequencer.get_input_names', return_value=["TestPort"])
    @patch('mido.open_input')
    def test_overwrite_truncation_in_listener(self, mock_open_input, mock_get_names):
        msg = mido.Message('note_on', note=60, velocity=100)
        self._setup_mock_port(mock_open_input, [[msg]])

        track = self.seq.song.tracks[0]
        track.record_mode = 'OVERWRITE'

        self.seq.last_record_settings = {'start_beat': 0.0}
        self.seq.is_recording = True
        self.seq.playback_state = 'playing'

        with patch.object(self.seq.jack_manager, '_get_input_routing_value', return_value=0):
            with patch.object(self.seq, '_truncate_track_for_recording') as mock_truncate:
                self.seq.set_default_record_port("TestPort")
                time.sleep(0.5)

                mock_truncate.assert_called_once()

if __name__ == '__main__':
    unittest.main()
