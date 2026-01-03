import sys
import os
from unittest.mock import patch, MagicMock

# Adjust path to import sequencer modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from sequencer.sequencer import Sequencer
from sequencer.models import MidiTrack

@patch('sequencer.sequencer.open_output')
@patch('sequencer.sequencer.get_output_names')
def run_verification(mock_get_outputs, mock_open_output):
    """
    Runs a series of checks to verify the virtual MIDI port assignment feature,
    with the mido library mocked to avoid environment errors.
    """
    # --- Mock Configuration ---
    mock_get_outputs.return_value = ['mock_system_port']

    def mock_port_factory(name, virtual=False):
        mock_port = MagicMock()
        mock_port.name = name
        mock_port.closed = False
        return mock_port

    mock_open_output.side_effect = mock_port_factory

    print("--- Starting Port Assignment Verification (with Mido mocked) ---")

    os.environ['KIVY_NO_WINDOW'] = '1'
    os.environ['KIVY_NO_ARGS'] = '1'

    try:
        from kivy.config import Config
        Config.set('graphics', 'width', '1')
        Config.set('graphics', 'height', '1')
        from kivy.app import App

        class TestApp(App):
            def build(self):
                return None

        app = TestApp()
        app.root = app.build()

        sequencer = Sequencer(gui_mode=False)

        # 1. Test initial state
        print("1. Verifying initial state...")
        initial_ports = sequencer.get_midi_output_ports()
        print(f"   Initial ports: {initial_ports}")
        assert isinstance(initial_ports, list)
        assert 'mock_system_port' in initial_ports
        print("   ✅ Initial port list is correct.")

        # 2. Add a MIDI track
        print("\n2. Adding a MIDI track...")
        sequencer.add_track("Test MIDI Track", "midi")
        track = sequencer.song.tracks[0]
        assert isinstance(track, MidiTrack)
        assert track.output_port_name is None
        print("   ✅ MIDI track added successfully.")

        # 3. Create a virtual port
        print("\n3. Creating a virtual port...")
        virtual_port_name = "MyTestVirtualPort"
        result = sequencer.create_virtual_port(virtual_port_name)
        assert "Error" not in result
        print(f"   Sequencer response: {result}")

        ports_after_creation = sequencer.get_midi_output_ports()
        print(f"   Ports after creation: {ports_after_creation}")
        assert virtual_port_name in ports_after_creation
        print(f"   ✅ Virtual port '{virtual_port_name}' created and found.")

        # 4. Assign the virtual port to the track
        print("\n4. Assigning virtual port to track...")
        sequencer.assign_midi_port_to_track(0, virtual_port_name)
        assert track.output_port_name == virtual_port_name
        print("   ✅ Track's port name is set.")

        # 5. Unassign the port
        print("\n5. Unassigning the port...")
        sequencer.assign_midi_port_to_track(0, None)
        assert track.output_port_name is None
        print("   ✅ Track's port name is unset.")

        # 6. Clean up
        print("\n6. Cleaning up virtual ports...")
        sequencer.delete_virtual_port(virtual_port_name)
        ports_after_deletion = sequencer.get_midi_output_ports()
        assert virtual_port_name not in ports_after_deletion
        print("   ✅ Virtual port successfully deleted.")

    except Exception as e:
        print(f"\n❌ VERIFICATION FAILED: An unexpected error occurred.")
        print(f"   ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    finally:
        if 'sequencer' in locals() and hasattr(sequencer, 'close_virtual_ports'):
            sequencer.close_virtual_ports()

    print("\n--- ✅ All Verification Checks Passed! ---")

if __name__ == "__main__":
    run_verification()
