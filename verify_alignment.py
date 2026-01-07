import sys
import os
import kivy
kivy.require('2.3.1')
from kivy.clock import Clock
from kivymd.app import MDApp
from sequencer.kivy_ui import SequencerLayout
from kivy.core.window import Window
import time
import mido

class AlignmentVerificationApp(MDApp):
    def build(self):
        # Use the specific project file for this verification
        project_file = os.path.abspath('automation_verification.proj.json')

        # Kivy's argument parser can be tricky. We manually ensure the project file is loaded.
        # Clear existing sys.argv that might be passed from the command line runner
        sys.argv = [sys.argv[0], '--', project_file]

        self.layout = SequencerLayout()
        return self.layout

    def on_start(self):
        # Give the app a generous amount of time to load and render everything.
        # Headless environments can be slower.
        Clock.schedule_once(self.take_screenshot, 5.0)

    def take_screenshot(self, dt):
        print("--- Taking Screenshot for Alignment Verification ---")
        try:
            screenshot_dir = '/home/jules/verification/'
            if not os.path.exists(screenshot_dir):
                os.makedirs(screenshot_dir)

            # Use a unique name to avoid conflicts
            screenshot_path = os.path.join(screenshot_dir, 'alignment_verification.png')

            Window.screenshot(name=screenshot_path)
            print(f"Screenshot requested at {screenshot_path}")

            # Schedule the check and stop, allowing time for file to write
            Clock.schedule_once(self.check_and_stop, 2.0)

        except Exception as e:
            print(f"Error during screenshot: {e}")
            self.stop()

    def check_and_stop(self, dt):
        screenshot_dir = '/home/jules/verification/'

        # Kivy often adds a number suffix, so we look for the file
        found_files = [f for f in os.listdir(screenshot_dir) if f.startswith('alignment_verification') and f.endswith('.png')]

        if found_files:
            print(f"Screenshot successfully saved as {found_files[0]}")
        else:
            print("Error: Screenshot file not found after waiting.")

        print("--- Verification Finished ---")
        self.stop()

if __name__ == '__main__':
    # --- Comprehensive MIDI Patch for Headless Environment ---
    # In a headless environment without a MIDI system, mido calls will fail.
    # We patch ALL functions that interact with the system to prevent errors.

    class MockMidoPort:
        """A mock object to replace mido port objects."""
        def __init__(self, name="mock_port"):
            self.name = name
            self.closed = False
        def close(self):
            self.closed = True
            print(f"--- MockMidoPort '{self.name}' closed. ---")
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc_val, exc_tb):
            self.close()

    def mock_get_ports(*args, **kwargs):
        print("--- Mido port scanning patched. ---")
        return []

    def mock_open_port(*args, **kwargs):
        print(f"--- Mido open_port patched for args={args} kwargs={kwargs}. ---")
        return MockMidoPort()

    print("--- Applying comprehensive MIDI patch for headless verification ---")
    mido.get_input_names = mock_get_ports
    mido.get_output_names = mock_get_ports
    mido.open_input = mock_open_port
    mido.open_output = mock_open_port

    # Ensure the 'src' directory is in the Python path
    sys.path.insert(0, os.path.abspath('src'))
    AlignmentVerificationApp().run()
