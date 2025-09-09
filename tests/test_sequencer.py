import unittest
from unittest.mock import patch, MagicMock, mock_open, call
from src.sequencer.sequencer import Sequencer
from src.sequencer.models import Song, MidiTrack, AudioTrack, Note, Event, CCMessage, AutomationTrack, AutomationPoint
import json
import mido

class TestSequencer(unittest.TestCase):

    def setUp(self):
        """Set up a new Sequencer instance before each test."""
        self.sequencer = Sequencer()

    def test_initialization(self):
        """Test that the sequencer initializes with default values."""
        self.assertEqual(self.sequencer.song.tempo, 120)
        self.assertEqual(self.sequencer.playback_state, "stopped")
        self.assertIsInstance(self.sequencer.song, Song)
        self.assertEqual(len(self.sequencer.song.tracks), 0)

    @patch('builtins.print')
    def test_set_tempo(self, mock_print):
        """Test the set_tempo method."""
        self.sequencer.set_tempo(150)
        self.assertEqual(self.sequencer.song.tempo, 150)
        mock_print.assert_called_with("Tempo set to 150 BPM.")

        with self.assertRaises(ValueError):
            self.sequencer.set_tempo(0)
        with self.assertRaises(ValueError):
            self.sequencer.set_tempo(-100)

    @patch('builtins.print')
    def test_set_time_signature(self, mock_print):
        """Test the set_time_signature method."""
        self.sequencer.set_time_signature(3, 4)
        self.assertEqual(self.sequencer.song.time_signature_numerator, 3)
        self.assertEqual(self.sequencer.song.time_signature_denominator, 4)
        mock_print.assert_called_with("Time signature set to 3/4.")

        # Test invalid denominator
        self.sequencer.set_time_signature(4, 5)
        mock_print.assert_called_with("Error: Invalid time signature. Denominator must be a power of 2.")

    @patch('builtins.print')
    def test_add_midi_track(self, mock_print):
        """Test adding a MIDI track."""
        self.sequencer.add_track(name="Test MIDI", track_type='midi', instrument=5)
        self.assertEqual(len(self.sequencer.song.tracks), 1)
        track = self.sequencer.song.tracks[0]
        self.assertIsInstance(track, MidiTrack)
        self.assertEqual(track.name, "Test MIDI")
        self.assertEqual(track.instrument, 5)
        mock_print.assert_called_with("MIDI track 'Test MIDI' added.")

    @patch('pydub.AudioSegment.from_file')
    @patch('builtins.print')
    def test_add_audio_track(self, mock_print, mock_from_file):
        """Test adding an Audio track."""
        mock_from_file.return_value = MagicMock() # Mock the successful loading of an audio file
        self.sequencer.add_track(name="Test Audio", track_type='audio', filepath="test.wav")
        self.assertEqual(len(self.sequencer.song.tracks), 1)
        track = self.sequencer.song.tracks[0]
        self.assertIsInstance(track, AudioTrack)
        self.assertEqual(track.name, "Test Audio")
        self.assertEqual(track.filepath, "test.wav")
        mock_print.assert_called_with("Audio track 'Test Audio' added with file 'test.wav'.")

    @patch('builtins.print')
    def test_delete_track(self, mock_print):
        """Test deleting a track."""
        self.sequencer.add_track(name="To Delete", track_type='midi')
        self.assertEqual(len(self.sequencer.song.tracks), 1)

        result = self.sequencer.delete_track(0)
        self.assertTrue(result)
        self.assertEqual(len(self.sequencer.song.tracks), 0)
        mock_print.assert_called_with("Track 'To Delete' deleted.")

        result = self.sequencer.delete_track(99)
        self.assertFalse(result)
        mock_print.assert_called_with("Error: Invalid track index.")

    @patch('builtins.print')
    def test_rename_track(self, mock_print):
        """Test renaming a track."""
        self.sequencer.add_track(name="Old Name", track_type='midi')
        self.sequencer.rename_track(0, "New Name")
        self.assertEqual(self.sequencer.song.tracks[0].name, "New Name")
        mock_print.assert_called_with("Track 'Old Name' renamed to 'New Name'.")

    def test_parse_position_to_beats(self):
        """Test the position string parsing logic."""
        self.sequencer.set_time_signature(4, 4)
        self.assertEqual(self.sequencer.parse_position_to_beats("1:1"), 0.0)
        self.assertEqual(self.sequencer.parse_position_to_beats("2:1"), 4.0)
        self.assertEqual(self.sequencer.parse_position_to_beats("3:3"), 10.0)
        self.assertIsNone(self.sequencer.parse_position_to_beats("1:5")) # Invalid beat
        self.assertIsNone(self.sequencer.parse_position_to_beats("abc")) # Invalid format

    @patch('builtins.print')
    def test_add_cc_event(self, mock_print):
        """Test adding a CC event to a track."""
        self.sequencer.add_track(name="MIDI", track_type='midi')
        self.sequencer.add_cc_event(track_index=0, position_str="1:2", control=7, value=100)

        track = self.sequencer.song.tracks[0]
        self.assertEqual(len(track.events), 1)
        event = track.events[0]
        self.assertEqual(event.start_time, 1.0) # 1:2 is the 2nd beat, which is at time 1.0
        self.assertEqual(len(event.cc_messages), 1)
        cc = event.cc_messages[0]
        self.assertEqual(cc.control, 7)
        self.assertEqual(cc.value, 100)
        mock_print.assert_called_with("Added new CC event at position 1:2 on track 'MIDI'.")

    @patch('mido.open_output')
    def test_assign_port(self, mock_open_output):
        """Test assigning a port to a track."""
        self.sequencer.add_track(name="MIDI", track_type='midi')
        self.sequencer.assign_port(0, "MyMIDIPort")
        self.assertEqual(self.sequencer.song.tracks[0].output_port_name, "MyMIDIPort")

    def test_save_and_load_project(self):
        """Test saving and loading a project file."""
        self.sequencer.add_track(name="Test MIDI", track_type='midi')
        self.sequencer.set_tempo(99)

        # Mock the writing of the project file
        m_write = mock_open()
        with patch('builtins.open', m_write):
            self.sequencer.save_project("test_project")

        # Get the content that was written to the mock file
        handle = m_write()
        written_content = "".join(call_args[0][0] for call_args in handle.write.call_args_list)

        # Mock the reading of the project file, using the content we just captured
        m_read = mock_open(read_data=written_content)
        with patch('builtins.open', m_read):
            new_sequencer = Sequencer()
            new_sequencer.load_project("test_project")

        # Assert that the loaded project has the correct data
        self.assertEqual(new_sequencer.song.tempo, 99)
        self.assertEqual(len(new_sequencer.song.tracks), 1)
        self.assertEqual(new_sequencer.song.tracks[0].name, "Test MIDI")

    @patch('builtins.print')
    def test_add_automation_track(self, mock_print):
        """Test adding an automation track."""
        self.sequencer.add_track(name="MIDI 1", track_type='midi')
        self.sequencer.add_automation_track(name="Volume Automation", target_track_index=0)

        self.assertEqual(len(self.sequencer.song.tracks), 2)
        auto_track = self.sequencer.song.tracks[1]
        self.assertIsInstance(auto_track, AutomationTrack)
        self.assertEqual(auto_track.name, "Volume Automation")
        self.assertEqual(auto_track.target_track_index, 0)
        mock_print.assert_called_with("Automation track 'Volume Automation' added, targeting track 0 ('MIDI 1').")

        # Test adding automation track targeting another automation track (should fail)
        self.sequencer.add_automation_track(name="Invalid Automation", target_track_index=1)
        mock_print.assert_called_with("Error: Automation tracks cannot target other automation tracks.")
        self.assertEqual(len(self.sequencer.song.tracks), 2) # Should not have been added

    @patch('builtins.print')
    def test_add_automation_point(self, mock_print):
        """Test adding an automation point to a track."""
        self.sequencer.add_track(name="MIDI 1", track_type='midi')
        self.sequencer.add_automation_track(name="Volume Automation", target_track_index=0)

        self.sequencer.add_automation_point(track_index=1, position_str="1:1", parameter="vol", value=0.5, curve="linear")

        auto_track = self.sequencer.song.tracks[1]
        self.assertEqual(len(auto_track.points), 1)
        point = auto_track.points[0]
        self.assertEqual(point.start_time, 0.0)
        self.assertEqual(point.parameter, "vol")
        self.assertEqual(point.value, 0.5)
        self.assertEqual(point.curve, "linear")
        mock_print.assert_called_with("Added 'vol' automation point to track 'Volume Automation' at position 1:1.")

        # Test adding to a non-automation track
        self.sequencer.add_automation_point(track_index=0, position_str="1:1", parameter="vol", value=0.5, curve="step")
        mock_print.assert_called_with("Error: Automation points can only be added to automation tracks.")

    @patch('src.sequencer.sequencer.open_output')
    def test_playback_with_automation(self, mock_open_output):
        """Test that automation events are correctly handled during playback."""
        mock_port = MagicMock()
        mock_open_output.return_value = mock_port

        self.sequencer.add_track(name="MIDI 1", track_type='midi')
        self.sequencer.song.tracks[0].output_port_name = 'test_port'
        self.sequencer.add_automation_track(name="Volume Automation", target_track_index=0)

        self.sequencer.add_automation_point(track_index=1, position_str="1:1", parameter="vol", value=0.5, curve="linear")
        self.sequencer.add_automation_point(track_index=1, position_str="1:2", parameter="vol", value=1.0, curve="step")

        note = Note(pitch=60, velocity=127, duration=4.0)
        event = Event(start_time=0.0, notes=[note])
        self.sequencer.song.tracks[0].add_event(event)

        # Let the playback run for a short time
        self.sequencer.play(start_beat=0.0, end_beat=2.0)
        import time
        time.sleep(0.5)
        self.sequencer.stop()

        # This is tricky because of threading, but we can check the messages that were sent.
        sent_messages = [call[0][0] for call in mock_port.send.call_args_list if isinstance(call[0][0], mido.Message)]

        # Check that a note_on message was sent
        self.assertIn(mido.Message('note_on', channel=0, note=60, velocity=127), sent_messages)

        # Check that CC messages for volume were sent.
        cc7_messages = [msg for msg in sent_messages if msg.type == 'control_change' and msg.control == 7]
        self.assertTrue(len(cc7_messages) > 1) # Should have sent at least the initial state and the first automation point

        # The order of initial state vs. automation at tick 0 is not guaranteed,
        # and automation should take precedence. We check that the ramp starts correctly.
        cc_values = [m.value for m in cc7_messages]
        self.assertIn(int(0.5 * 127), cc_values) # First automation point should be present
        self.assertTrue(cc_values[-1] > int(0.5*127)) # Ramp should be going up

    @patch('builtins.print')
    def test_set_track_pan(self, mock_print):
        """Test setting the pan for a track."""
        self.sequencer.add_track(name="MIDI 1", track_type='midi')
        self.sequencer.set_track_pan(0, -0.5)
        self.assertEqual(self.sequencer.song.tracks[0].pan, -0.5)
        mock_print.assert_called_with("Pan for track 'MIDI 1' set to -0.50.")

    @patch('builtins.input', side_effect=['1:1', '2:1', 'n', 'y', 'n']) # Add 'n' for shift prompt
    @patch('builtins.print')
    def test_erase_track_notes_only(self, mock_print, mock_input):
        """Test that erase command can remove only notes."""
        self.sequencer.add_track(name="Test Track", track_type='midi')
        track = self.sequencer.song.tracks[0]
        track.add_event(Event(start_time=0.0, notes=[Note(pitch=60, velocity=100, duration=1.0)], cc_messages=[CCMessage(control=7, value=100)]))
        track.add_event(Event(start_time=2.0, notes=[Note(pitch=62, velocity=100, duration=1.0)]))

        self.sequencer.erase_track(0)

        # After erasing notes, the first event should still exist because of the CC.
        # The second event, which only contained a note, should be removed.
        self.assertEqual(len(track.events), 1)
        self.assertEqual(len(track.events[0].notes), 0)
        self.assertEqual(len(track.events[0].cc_messages), 1)
        self.assertEqual(track.events[0].cc_messages[0].control, 7)

    @patch('src.sequencer.sequencer.threading.Thread')
    def test_record_replace_notes_only(self, mock_thread):
        """Test that recording with 'replace' only removes notes."""
        self.sequencer.add_track(name="Test Track", track_type='midi')
        track = self.sequencer.song.tracks[0]
        track.add_event(Event(start_time=1.0, notes=[Note(pitch=60, velocity=100, duration=1.0)], cc_messages=[CCMessage(control=7, value=100)]))

        # Call the internal method directly to test the replacement logic
        self.sequencer._start_recording_internal(track_index=0, start_beat=0.0, num_beats_to_record=4.0, inport_name='dummy', replace_notes=True)

        # Check that the event still exists but the note is gone
        self.assertEqual(len(track.events), 1)
        self.assertEqual(len(track.events[0].notes), 0)
        self.assertEqual(len(track.events[0].cc_messages), 1)
        self.assertEqual(track.events[0].cc_messages[0].control, 7)

    @patch('builtins.input', side_effect=['1:1', '', 'vol', 'y'])
    def test_erase_automation_track_specific_param(self, mock_input):
        """Test erasing a specific parameter from an automation track."""
        self.sequencer.add_track(name="Target", track_type='midi')
        self.sequencer.add_automation_track(name="Auto", target_track_index=0)
        auto_track = self.sequencer.song.tracks[1]
        auto_track.add_point(AutomationPoint(start_time=0.0, parameter="vol", value=0.5))
        auto_track.add_point(AutomationPoint(start_time=1.0, parameter="pan", value=-0.5))
        auto_track.add_point(AutomationPoint(start_time=2.0, parameter="vol", value=1.0))

        self.sequencer.erase_track(1)

        self.assertEqual(len(auto_track.points), 1)
        self.assertEqual(auto_track.points[0].parameter, "pan")

    @patch('builtins.input', side_effect=['1:1', '5:1', 'all', 'y'])
    def test_erase_automation_track_all_params_in_range(self, mock_input):
        """Test erasing all parameters from an automation track in a range."""
        self.sequencer.add_track(name="Target", track_type='midi')
        self.sequencer.add_automation_track(name="Auto", target_track_index=0)
        auto_track = self.sequencer.song.tracks[1]
        auto_track.add_point(AutomationPoint(start_time=0.0, parameter="vol", value=0.5))
        auto_track.add_point(AutomationPoint(start_time=1.0, parameter="pan", value=-0.5))
        auto_track.add_point(AutomationPoint(start_time=5.0, parameter="vol", value=1.0)) # This one is outside the erase range

        self.sequencer.erase_track(1)

        self.assertEqual(len(auto_track.points), 0)

    def test_new_project(self):
        """Test creating a new project."""
        # Modify the current project
        self.sequencer.add_track(name="Piano", track_type='midi')
        self.sequencer.set_tempo(150)
        self.sequencer.last_project_basename = "old_project"
        self.sequencer.is_dirty = True

        # Create a new project
        with patch.object(self.sequencer, 'close_virtual_ports') as mock_close_vp:
            self.sequencer.new_project()

            # Check that the state is reset
            self.assertEqual(self.sequencer.song.name, "New Song")
            self.assertEqual(self.sequencer.song.tempo, 120)
            self.assertEqual(len(self.sequencer.song.tracks), 0)
            self.assertFalse(self.sequencer.is_dirty)
            self.assertIsNone(self.sequencer.last_project_basename)
            mock_close_vp.assert_called_once()

    @patch('src.sequencer.sequencer.threading.Thread')
    def test_overdub_does_not_mute(self, mock_thread):
        """Test that overdubbing does not mute the track."""
        self.sequencer.add_track(name="Test Track", track_type='midi')
        track = self.sequencer.song.tracks[0]
        track.is_muted = False

        # Call the internal method directly to test the logic
        self.sequencer._start_recording_internal(track_index=0, start_beat=0.0, num_beats_to_record=4.0, inport_name='dummy', replace_notes=False)

        # Check that the track is not muted
        self.assertFalse(track.is_muted)


if __name__ == '__main__':
    unittest.main()
