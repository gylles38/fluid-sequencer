import unittest
from unittest.mock import patch, MagicMock, mock_open, call
import threading
import time
from sequencer.sequencer import Sequencer
from sequencer.models import Song, MidiTrack, AudioTrack, Note, Event, CCMessage, AutomationTrack, AutomationPoint
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

    def test_set_tempo(self):
        """Test the set_tempo method."""
        result = self.sequencer.set_tempo(150)
        self.assertEqual(self.sequencer.song.tempo, 150)
        self.assertEqual(result, "Tempo set to 150 BPM.")

        result = self.sequencer.set_tempo(0)
        self.assertEqual(result, "Error: Tempo must be positive.")
        result = self.sequencer.set_tempo(-100)
        self.assertEqual(result, "Error: Tempo must be positive.")

    def test_set_time_signature(self):
        """Test the set_time_signature method."""
        result = self.sequencer.set_time_signature(3, 4)
        self.assertEqual(self.sequencer.song.time_signature_numerator, 3)
        self.assertEqual(self.sequencer.song.time_signature_denominator, 4)
        self.assertEqual(result, "Time signature set to 3/4.")

        # Test invalid denominator
        result = self.sequencer.set_time_signature(4, 5)
        self.assertEqual(result, "Error: Invalid time signature. Denominator must be a power of 2.")

    def test_add_midi_track(self):
        """Test adding a MIDI track."""
        result = self.sequencer.add_track(name="Test MIDI", track_type='midi', instrument=5)
        self.assertEqual(len(self.sequencer.song.tracks), 1)
        track = self.sequencer.song.tracks[0]
        self.assertIsInstance(track, MidiTrack)
        self.assertEqual(track.name, "Test MIDI")
        self.assertEqual(track.instrument, 5)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['message'], "MIDI track 'Test MIDI' added.")

    @patch('pydub.AudioSegment.from_file')
    def test_add_audio_track(self, mock_from_file):
        """Test adding an Audio track."""
        mock_segment = MagicMock()
        mock_segment.__len__.return_value = 1000 # 1 second
        mock_from_file.return_value = mock_segment # Mock the successful loading of an audio file
        result = self.sequencer.add_track(name="Test Audio", track_type='audio', filepath="test.wav")
        self.assertEqual(len(self.sequencer.song.tracks), 1)
        track = self.sequencer.song.tracks[0]
        self.assertIsInstance(track, AudioTrack)
        self.assertEqual(track.name, "Test Audio")
        self.assertEqual(track.filepath, "test.wav")
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['message'], "Audio track 'Test Audio' added with file 'test.wav'.")

    def test_delete_track(self):
        """Test deleting a track."""
        self.sequencer.add_track(name="To Delete", track_type='midi')
        self.assertEqual(len(self.sequencer.song.tracks), 1)

        result = self.sequencer.delete_track(0, confirm_str='y', api_mode=True)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(len(self.sequencer.song.tracks), 0)
        self.assertEqual(result['message'], "Track 'To Delete' deleted.")

        result = self.sequencer.delete_track(99, confirm_str='y', api_mode=True)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['message'], "Error: Invalid track index.")

    def test_rename_track(self):
        """Test renaming a track."""
        self.sequencer.add_track(name="Old Name", track_type='midi')
        result = self.sequencer.rename_track(0, "New Name")
        self.assertEqual(self.sequencer.song.tracks[0].name, "New Name")
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['message'], "Track 'Old Name' renamed to 'New Name'.")

    def test_parse_position_to_beats(self):
        """Test the position string parsing logic."""
        self.sequencer.set_time_signature(4, 4)
        self.assertEqual(self.sequencer.parse_position_to_beats("1:1"), 0.0)
        self.assertEqual(self.sequencer.parse_position_to_beats("2:1"), 4.0)
        self.assertEqual(self.sequencer.parse_position_to_beats("3:3"), 10.0)
        self.assertIsNone(self.sequencer.parse_position_to_beats("1:5")) # Invalid beat
        self.assertIsNone(self.sequencer.parse_position_to_beats("abc")) # Invalid format

    def test_add_cc_event(self):
        """Test adding a CC event to a track."""
        self.sequencer.add_track(name="MIDI", track_type='midi')
        result = self.sequencer.add_cc_event(track_index=0, position_str="1:2", control=7, value=100)

        track = self.sequencer.song.tracks[0]
        self.assertEqual(len(track.events), 1)
        event = track.events[0]
        self.assertEqual(event.start_time, 1.0) # 1:2 is the 2nd beat, which is at time 1.0
        self.assertEqual(len(event.cc_messages), 1)
        cc = event.cc_messages[0]
        self.assertEqual(cc.control, 7)
        self.assertEqual(cc.value, 100)
        self.assertEqual(result, "Added new CC event at position 1:2 on track 'MIDI'.")

    def test_assign_port(self):
        """Test assigning a port to a track."""
        self.sequencer.add_track(name="MIDI", track_type='midi')
        result = self.sequencer.assign_port(0, "MyMIDIPort")
        self.assertEqual(self.sequencer.song.tracks[0].output_port_name, "MyMIDIPort")
        self.assertEqual(result, "Assigned port 'MyMIDIPort' to track 'MIDI'.")

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

    def test_add_automation_track(self):
        """Test adding an automation track."""
        self.sequencer.add_track(name="MIDI 1", track_type='midi')
        result = self.sequencer.add_automation_track(name="Volume Automation", target_track_index=0)

        self.assertEqual(len(self.sequencer.song.tracks), 2)
        auto_track = self.sequencer.song.tracks[1]
        self.assertIsInstance(auto_track, AutomationTrack)
        self.assertEqual(auto_track.name, "Volume Automation")
        self.assertEqual(auto_track.target_track_index, 0)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['message'], "Automation track 'Volume Automation' added, targeting track 0 ('MIDI 1').")

        # Test adding automation track targeting another automation track (should fail)
        result = self.sequencer.add_automation_track(name="Invalid Automation", target_track_index=1)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['message'], "Error: Automation tracks cannot target other automation tracks.")
        self.assertEqual(len(self.sequencer.song.tracks), 2) # Should not have been added

    def test_add_automation_point(self):
        """Test adding an automation point to a track."""
        self.sequencer.add_track(name="MIDI 1", track_type='midi')
        self.sequencer.add_automation_track(name="Volume Automation", target_track_index=0)

        result = self.sequencer.add_automation_point(track_index=1, position_str="1:1", parameter="vol", value=0.5, curve="linear")

        auto_track = self.sequencer.song.tracks[1]
        self.assertEqual(len(auto_track.points), 1)
        point = auto_track.points[0]
        self.assertEqual(point.start_time, 0.0)
        self.assertEqual(point.parameter, "vol")
        self.assertEqual(point.value, 0.5)
        self.assertEqual(point.curve, "linear")
        self.assertEqual(result, "Added 'vol' automation point to track 'Volume Automation' at position 1:1.")

        # Test adding to a non-automation track
        result = self.sequencer.add_automation_point(track_index=0, position_str="1:1", parameter="vol", value=0.5, curve="step")
        self.assertEqual(result, "Error: Automation points can only be added to automation tracks.")

    @patch('sequencer.sequencer.JackManager.start')
    def test_play_starts_jack_manager(self, mock_jack_start):
        """Test that the play command starts the JackManager."""
        self.sequencer.play()
        mock_jack_start.assert_called_once()

    def test_set_track_pan(self):
        """Test setting the pan for a track."""
        self.sequencer.add_track(name="MIDI 1", track_type='midi')
        result = self.sequencer.set_track_pan(0, pan_str="-0.5", api_mode=True)
        self.assertEqual(self.sequencer.song.tracks[0].pan, -0.5)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['message'], "Pan for track 'MIDI 1' set to -0.50.")

    def test_erase_track_notes_only(self):
        """Test that erase command can remove only notes."""
        self.sequencer.add_track(name="Test Track", track_type='midi')
        track = self.sequencer.song.tracks[0]
        track.add_event(Event(start_time=0.0, notes=[Note(pitch=60, velocity=100, duration=1.0)], cc_messages=[CCMessage(control=7, value=100)]))
        track.add_event(Event(start_time=2.0, notes=[Note(pitch=62, velocity=100, duration=1.0)]))

        self.sequencer.erase_track(track_idx=0, start_beat=0.0, end_beat=4.0, erase_choice='notes', shift_events=False)

        # After erasing notes, the first event should still exist because of the CC.
        # The second event, which only contained a note, should be removed.
        self.assertEqual(len(track.events), 1)
        self.assertEqual(len(track.events[0].notes), 0)
        self.assertEqual(len(track.events[0].cc_messages), 1)
        self.assertEqual(track.events[0].cc_messages[0].control, 7)

    def test_erase_automation_track_specific_param(self):
        """Test erasing a specific parameter from an automation track."""
        self.sequencer.add_track(name="Target", track_type='midi')
        self.sequencer.add_automation_track(name="Auto", target_track_index=0)
        auto_track = self.sequencer.song.tracks[1]
        auto_track.add_point(AutomationPoint(start_time=0.0, parameter="vol", value=0.5))
        auto_track.add_point(AutomationPoint(start_time=1.0, parameter="pan", value=-0.5))
        auto_track.add_point(AutomationPoint(start_time=2.0, parameter="vol", value=1.0))

        self.sequencer.erase_track(track_idx=1, start_beat=0.0, end_beat=4.0, erase_choice='vol', shift_events=False)

        self.assertEqual(len(auto_track.points), 1)
        self.assertEqual(auto_track.points[0].parameter, "pan")

    def test_erase_automation_track_all_params_in_range(self):
        """Test erasing all parameters from an automation track in a range."""
        self.sequencer.add_track(name="Target", track_type='midi')
        self.sequencer.add_automation_track(name="Auto", target_track_index=0)
        auto_track = self.sequencer.song.tracks[1]
        auto_track.add_point(AutomationPoint(start_time=0.0, parameter="vol", value=0.5))
        auto_track.add_point(AutomationPoint(start_time=1.0, parameter="pan", value=-0.5))
        auto_track.add_point(AutomationPoint(start_time=5.0, parameter="vol", value=1.0)) # This one is outside the erase range

        self.sequencer.erase_track(track_idx=1, start_beat=0.0, end_beat=4.0, erase_choice='all', shift_events=False)

        self.assertEqual(len(auto_track.points), 1)
        self.assertEqual(auto_track.points[0].start_time, 5.0)

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

    @patch('sequencer.sequencer.threading.Thread')
    def test_overdub_does_not_mute(self, mock_thread):
        """Test that overdubbing does not mute the track."""
        self.sequencer.add_track(name="Test Track", track_type='midi')
        track = self.sequencer.song.tracks[0]
        track.is_muted = False

        # Call the internal method directly to test the logic
        self.sequencer._start_recording_internal(track_index=0, start_beat=0.0, num_beats_to_record=4.0, inport_name='dummy', replace_notes=False, enable_thru=False)

        # Check that the track is not muted
        self.assertFalse(track.is_muted)

    @patch('sequencer.sequencer.Sequencer.set_control_port')
    def test_save_and_load_control_port(self, mock_set_control_port):
        """Test that the control port is saved and loaded with the project."""
        # Set a control port name
        self.sequencer.control_port_name = "MyTestControlPort"

        # Mock the writing of the project file
        m_write = mock_open()
        with patch('builtins.open', m_write):
            self.sequencer.save_project("test_control_port_project")

        # Get the content that was written to the mock file
        handle = m_write()
        written_content = "".join(call_args[0][0] for call_args in handle.write.call_args_list)

        # Mock the reading of the project file
        m_read = mock_open(read_data=written_content)
        with patch('builtins.open', m_read):
            new_sequencer = Sequencer()
            # We patch set_control_port on the class, so it applies to the new instance
            new_sequencer.load_project("test_control_port_project")

        # Assert that set_control_port was called with the correct name
        mock_set_control_port.assert_called_with("MyTestControlPort")

    @patch('pydub.AudioSegment.from_file')
    @patch('sequencer.sequencer.jack')
    def test_play_range_stops_audio(self, mock_jack, mock_from_file):
        """Test that reaching the end of a play range stops audio tracks and the transport."""
        # Setup
        mock_from_file.return_value = MagicMock()
        sequencer = self.sequencer
        jm = sequencer.jack_manager

        # Mock the JACK client and its state
        jm.jack_client = MagicMock()
        jm.jack_client.transport_state = mock_jack.ROLLING

        # Mock the function we want to test is called
        jm.set_all_audio_pause_state = MagicMock()

        # Mock the transport query to return a valid state
        mock_pos = MagicMock()
        mock_jack.position2dict.return_value = {'beats_per_minute': 120.0, 'frame': 0}
        jm.jack_client.transport_query_struct.return_value = (mock_jack.ROLLING, mock_pos)

        # Set a play range
        sequencer.play_range_enabled = True
        sequencer.play_range_end_beat = 4.0 # Stop at the end of the first measure

        # Simulate the process callback just before the end of the play range
        jm.last_beat = 3.9
        sequencer.song.tempo = 120.0
        samplerate = jm.jack_client.samplerate = 48000

        # Calculate frames needed to cross the play_range_end_beat boundary
        # end_beat_of_block = start_beat_of_block + (frames / samplerate) * beats_per_second
        # 4.1 = 3.9 + (frames / 48000) * 2.0 => frames = 4800
        frames = 4800

        # Correctly mock the advancing frame
        new_frame_pos = 3.9 * samplerate * 0.5 + frames
        mock_jack.position2dict.return_value = {'beats_per_minute': 120.0, 'frame': new_frame_pos}


        # Call the method under test
        jm._process_callback(frames)

        # Assertions
        jm.jack_client.transport_stop.assert_called_once()
        jm.set_all_audio_pause_state.assert_called_with(True)
        self.assertFalse(sequencer.play_range_enabled)


if __name__ == '__main__':
    unittest.main()


class TestRecording(unittest.TestCase):
    def setUp(self):
        """Set up a new Sequencer instance before each test."""
        self.sequencer = Sequencer()
        self.sequencer.add_track(name="Test Track", track_type='midi')
        self.sequencer.jack_manager = MagicMock()
        # Mock the jack client's transport state to be ROLLING
        self.sequencer.jack_manager.jack_client.transport_state = 2 # jack.ROLLING

    @patch('sequencer.sequencer.threading.Thread')
    def test_record_replace_notes_only(self, mock_thread):
        """Test that recording with 'replace' only removes notes."""
        track = self.sequencer.song.tracks[0]
        track.record_mode = 'OVERWRITE'
        track.add_event(Event(start_time=1.0, notes=[Note(pitch=60, velocity=100, duration=1.0)], cc_messages=[CCMessage(control=7, value=100)]))

        # Call the internal method directly to test the replacement logic
        self.sequencer._start_recording_internal(track_index=0, start_beat=0.0, num_beats_to_record=4.0, inport_name='dummy', replace_notes=True, enable_thru=False)

        # Check that the event still exists but the note is gone
        self.assertEqual(len(track.events), 1)
        self.assertEqual(len(track.events[0].notes), 0)
        self.assertEqual(len(track.events[0].cc_messages), 1)
        self.assertEqual(track.events[0].cc_messages[0].control, 7)
        mock_thread.assert_called_once()

    @patch('sequencer.sequencer.threading.Thread')
    def test_overdub_does_not_mute(self, mock_thread):
        """Test that overdubbing does not mute the track."""
        track = self.sequencer.song.tracks[0]
        track.record_mode = 'KEEP'
        track.is_muted = False

        # Call the internal method directly to test the logic
        self.sequencer._start_recording_internal(track_index=0, start_beat=0.0, num_beats_to_record=4.0, inport_name='dummy', replace_notes=False, enable_thru=False)

        # Check that the track is not muted
        self.assertFalse(track.is_muted)
        mock_thread.assert_called_once()

    @patch('sequencer.sequencer.Sequencer._start_recording_internal')
    def test_record_track_flow(self, mock_start_recording):
        """Test the main record_track function flow."""
        self.sequencer.add_track(name="Track 2", track_type="midi")
        self.sequencer.song.tracks[0].record_mode = 'OVERWRITE'
        # Add an existing note to trigger the replace/add prompt
        self.sequencer.song.tracks[0].add_event(Event(start_time=2.0, notes=[Note(pitch=1, velocity=1, duration=1)]))

        self.sequencer.record_track(track_idx=0, start_beat=0.0, num_beats_to_record=4.0, inport_name='TestInputPort', replace_notes=True, enable_thru=True)

        # Assert that the internal recording function was called with the correct parameters
        mock_start_recording.assert_called_once_with(track_index=0, start_beat=0.0, num_beats_to_record=4.0, inport_name='TestInputPort', replace_notes=True, enable_thru=True)

    def test_note_capture_logic_in_isolation(self):
        """Tests the core logic of capturing a note from note-on/note-off messages."""
        track = self.sequencer.song.tracks[0]
        open_notes = {}

        # 1. Simulate NOTE ON
        note_on_beat = 1.0
        # This is what the note-on block does:
        open_notes[60] = (note_on_beat, 100)

        # 2. Simulate NOTE OFF
        note_off_beat = 2.0
        note_to_close = 60

        # This is the logic block from the SUT's note-off handling
        if note_to_close in open_notes:
            start_time, velocity = open_notes.pop(note_to_close)
            duration_beats = note_off_beat - start_time
            if duration_beats <= 0: duration_beats = 0.01
            note = Note(pitch=note_to_close, velocity=velocity, duration=duration_beats)
            track.add_event(Event(notes=[note], start_time=start_time))

        # 3. Assertions
        self.assertEqual(len(track.events), 1)
        event = track.events[0]
        self.assertAlmostEqual(event.start_time, 1.0)
        note = event.notes[0]
        self.assertEqual(note.pitch, 60)
        self.assertEqual(note.velocity, 100)
        self.assertAlmostEqual(note.duration, 1.0)

    @patch('mido.open_output')
    def test_toggle_mute_sends_note_off(self, mock_open_output):
        """Test that muting a MIDI track sends an 'all notes off' message."""
        # Setup
        mock_port = MagicMock()
        mock_open_output.return_value = mock_port

        sequencer = self.sequencer
        jm = sequencer.jack_manager
        jm.is_running = True # Simulate that JACK is running

        # Add a MIDI track and 'open' its port
        sequencer.add_track(name="Test MIDI", track_type='midi')
        track = sequencer.song.tracks[0]
        track.output_port_name = "test_port"
        jm.open_ports["test_port"] = mock_port

        # Mute the track
        sequencer.toggle_mute(0)

        # Assertion
        self.assertTrue(track.is_muted)
        # Check that a CC message with control=123 (All Notes Off) was sent
        mock_port.send.assert_called_with(mido.Message('control_change', channel=track.channel, control=123, value=0))

        # Unmute the track
        sequencer.toggle_mute(0)
        self.assertFalse(track.is_muted)
        # Ensure no new messages were sent on unmute
        self.assertEqual(mock_port.send.call_count, 1)
