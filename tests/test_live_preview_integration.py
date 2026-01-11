import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Ensure the 'src' directory is in the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from sequencer.sequencer import Sequencer
from sequencer.models import MidiTrack, Note, Event
from copy import deepcopy

class TestLivePreviewIntegration(unittest.TestCase):

    def setUp(self):
        """Set up a sequencer instance for testing."""
        self.sequencer = Sequencer(gui_mode=True)
        # Add a simple MIDI track with one note for testing
        note = Note(pitch=60, velocity=100, duration=1.0)
        event = Event(start_time=0.0, notes=[note])
        track = MidiTrack(name="Test Track", events=[event], output_port_name="TestPort")
        self.sequencer.song.add_track(track)

    @patch('mido.open_output')
    @patch('jack.Client')
    def test_playback_uses_overridden_track(self, mock_jack_client, mock_open_output):
        """
        Verify that the sequencer's playback engine uses the track from
        `track_overrides` if it exists.
        """
        # --- Setup ---
        # Mock the Jack manager and its open ports
        jack_manager = self.sequencer.jack_manager
        mock_port = MagicMock()
        mock_open_output.return_value = mock_port
        jack_manager.open_ports['TestPort'] = mock_port

        # Initialize the event indices for playback processing
        jack_manager._sync_playhead_to_beat(0.0)

        # --- Simulate Editing ---
        # Create a manual deep copy of the track to simulate an editor modifying it
        # Kivy's EventDispatcher (which MidiTrack inherits from) doesn't support deepcopy.
        original_track_index = 0
        original_track = self.sequencer.song.tracks[original_track_index]
        track_copy = MidiTrack(
            name=original_track.name,
            is_muted=original_track.is_muted,
            is_solo=original_track.is_solo,
            channel=original_track.channel,
            volume=original_track.volume,
            pan=original_track.pan,
            velocity=original_track.velocity,
            events=deepcopy(original_track.events), # Events list is safe to deepcopy
            instrument=original_track.instrument,
            output_port_name=original_track.output_port_name
        )

        # Modify the note in the copied track
        modified_pitch = 62
        track_copy.events[0].notes[0].pitch = modified_pitch

        # Place the modified copy in the track_overrides dictionary
        self.sequencer.track_overrides[original_track_index] = track_copy

        # --- Simulate Playback ---
        # Process a block of time that includes the note
        start_beat = 0.0
        end_beat = 1.0
        jack_manager._process_midi_events(start_beat, end_beat)

        # --- Assertion ---
        # Check that the MIDI message sent has the MODIFIED pitch
        mock_port.send.assert_called_once()
        sent_message = mock_port.send.call_args[0][0]

        self.assertEqual(sent_message.type, 'note_on')
        self.assertEqual(sent_message.note, modified_pitch,
                         "Playback did not use the modified note pitch from the overridden track.")

        # --- Cleanup ---
        self.sequencer.track_overrides.clear()

        # --- Verify Original Data ---
        # Reset the mock and play again without the override to ensure the original is unchanged
        mock_port.reset_mock()
        jack_manager._sync_playhead_to_beat(0.0) # Reset playhead
        jack_manager._process_midi_events(start_beat, end_beat)

        mock_port.send.assert_called_once()
        sent_message_original = mock_port.send.call_args[0][0]
        self.assertEqual(sent_message_original.note, 60,
                         "The original track data was altered during the override test.")


if __name__ == '__main__':
    unittest.main()
