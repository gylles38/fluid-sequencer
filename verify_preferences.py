import kivy
kivy.require('2.3.1')
from kivymd.app import MDApp
from kivy.clock import Clock
from kivy.core.window import Window
import os
import sys
import unittest
import json
from unittest.mock import patch

# Ensure the src directory is in the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from sequencer.sequencer import Sequencer
from sequencer.kivy_ui import SequencerLayout
from sequencer.ui_components.PreferencesPopup import PreferencesPopup
from sequencer.ui_components.FileChooserPopup import FileChooserPopup
from sequencer.config_manager import ConfigManager
from kivymd.uix.button import MDButton


class TestPreferencesPopup(unittest.TestCase):

    def setUp(self):
        # Create a dummy config file for the test
        self.test_config_path = '/tmp/test_sequencer_config.json'
        if os.path.exists(self.test_config_path):
            os.remove(self.test_config_path)

        self.app = None
        self.layout = None
        self.timeout_event = Clock.schedule_once(self.stop_app, 20) # Timeout to prevent hanging

    def tearDown(self):
        self.timeout_event.cancel()
        if os.path.exists(self.test_config_path):
            os.remove(self.test_config_path)
        if self.app:
            self.app.stop()

    def stop_app(self, dt=0):
        print("Timeout reached. Stopping app.")
        if self.app:
            self.app.stop()

    def run_test_app(self, test_logic):
        # Create a specific ConfigManager instance for this test run
        test_config_manager = ConfigManager(config_path=self.test_config_path)

        class TestApp(MDApp):
            def build(s):
                # Disable the initial MIDI port selection popup to avoid blocking the test
                s.layout = SequencerLayout()
                s.layout.sequencer.default_record_port = "dummy_port"

                # *** INJECT TEST CONFIG MANAGER HERE ***
                s.layout.sequencer.config_manager = test_config_manager

                Clock.schedule_once(lambda dt: test_logic(s.layout), 1.5) # Increased delay for stability
                return s.layout

        self.app = TestApp()
        self.app.run()

    def test_preferences_flow(self):
        """Test the entire preferences change and verification flow."""

        def test_logic(layout):
            print("--- Starting Preferences Test ---")

            # 1. Open the Preferences popup
            print("1. Opening Preferences Popup...")
            layout.show_preferences_popup()

            # Find the popup
            popup = None
            for child in Window.children:
                if isinstance(child, PreferencesPopup):
                    popup = child
                    break

            self.assertIsNotNone(popup, "PreferencesPopup did not open.")
            print("   - Preferences Popup is open.")

            # 2. Change a value
            new_path = "/tmp/my_test_projects"
            if not os.path.exists(new_path):
                os.makedirs(new_path)

            projects_path_input = popup.path_inputs.get('default_projects_dir')
            self.assertIsNotNone(projects_path_input, "Projects path input not found in popup.")

            print(f"2. Changing default_projects_dir to '{new_path}'...")
            projects_path_input.text = new_path

            # 3. Save the preferences by finding and dispatching press on the Save button
            print("3. Clicking 'Save'...")

            buttons_layout = popup.content.children[0]
            save_button = None
            for button in buttons_layout.children:
                if isinstance(button, MDButton) and hasattr(button, 'children') and button.children[0].text == "Save":
                     save_button = button
                     break

            self.assertIsNotNone(save_button, "Could not find 'Save' button in popup.")
            save_button.dispatch('on_press')
            print("   - 'Save' dispatched.")

            # 4. Verify the config file was saved with the new value
            print("4. Verifying config file content...")
            self.assertTrue(os.path.exists(self.test_config_path), f"Config file was not created at {self.test_config_path}.")

            with open(self.test_config_path, 'r') as f:
                config_data = json.load(f)

            self.assertEqual(config_data.get('default_projects_dir'), new_path, "Config file does not contain the new path.")
            print("   - Config file verified.")

            # 5. Open the "Load Project" dialog and verify the path
            print('5. Opening "Load Project" popup to verify path...')
            layout.load_project_popup()

            file_popup = None
            # The popup might take a frame to appear
            def check_for_file_popup(dt):
                nonlocal file_popup
                for child in Window.children:
                    if isinstance(child, FileChooserPopup) and "Load Project" in child.title:
                        file_popup = child
                        # Stop this scheduled check
                        return False
                return True # continue scheduling

            Clock.schedule_interval(check_for_file_popup, 0.1)

            # Wait for the popup to be found
            start_time = Clock.get_time()
            while file_popup is None and (Clock.get_time() - start_time) < 5:
                Clock.tick() # Process Kivy events

            self.assertIsNotNone(file_popup, '"Load Project" popup did not open.')
            print('   - "Load Project" popup is open.')

            # 6. Check the initial path of the file chooser
            print("6. Checking FileChooser path...")
            actual_path = file_popup.filechooser.path
            self.assertEqual(actual_path, new_path, f"FileChooser opened with path '{actual_path}' instead of expected '{new_path}'.")
            print("   - FileChooser path is correct.")

            print("--- Test Passed ---")

            self.stop_app()

        self.run_test_app(test_logic)


if __name__ == '__main__':
    # This script is designed to be run inside an xvfb environment
    # Example: PYTHONPATH=src xvfb-run -a python3 verify_preferences.py
    # We will manually instantiate and run the test.
    test = TestPreferencesPopup()
    try:
        test.setUp()
        test.test_preferences_flow()
        print("\nVerification script finished successfully.")
    except Exception as e:
        print(f"\nVerification script failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        test.tearDown()
