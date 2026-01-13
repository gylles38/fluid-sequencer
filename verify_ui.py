
import os
import sys
# Add the 'src' directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from kivy.clock import Clock
from kivymd.app import MDApp
from kivy.core.window import Window
from sequencer.kivy_ui import SequencerLayout
from sequencer.sequencer import Sequencer
from sequencer.ui_components.TrackWidget import TrackWidget, AutomationGrid

class VerifyApp(MDApp):
    def build(self):
        self.theme_cls.theme_style = "Dark"
        self.sequencer = Sequencer()

        # This prevents the MIDI port selection popup from appearing
        # and blocking the script.
        self.sequencer.default_midi_out_port = "fake_port"

        # Pass the sequencer as a keyword argument to the constructor
        layout = SequencerLayout(sequencer=self.sequencer)

        # Load the project after the layout is created
        Clock.schedule_once(lambda dt: self.load_and_verify(layout))

        return layout

    def load_and_verify(self, layout):
        project_path = os.path.abspath('projects/verify_automation.proj.json')
        print(f"Loading project from: {project_path}")
        layout.sequencer.load_project(project_path)
        layout.update_status_display()

        # Wait a moment for the UI to update after loading the project
        Clock.schedule_once(lambda dt: self.open_editor_and_screenshot(layout), 2)

    def open_editor_and_screenshot(self, layout):
        print("Searching for AutomationGrid...")
        automation_grid = None
        # Find the AutomationGrid widget in the track widgets
        for widget in layout.walk():
            if isinstance(widget, AutomationGrid):
                automation_grid = widget
                break

        if automation_grid:
            print("Found AutomationGrid. Opening editor...")
            # Simulate the double-tap by calling the method directly
            automation_grid.track_widget.open_automation_editor()
            # Wait for the editor to open and draw
            Clock.schedule_once(self.take_screenshot, 2)
        else:
            print("Error: AutomationGrid not found.")
            self.stop()

    def take_screenshot(self, dt):
        print("Taking screenshot...")
        if not os.path.exists('verification'):
            os.makedirs('verification')
        screenshot_path = 'verification/automation_editor_verify.png'
        Window.screenshot(name=screenshot_path)
        print(f"Screenshot saved to {screenshot_path}")
        # After taking the screenshot, stop the app
        self.stop()

if __name__ == '__main__':
    # Ensure the script is run from the project root
    if not os.path.exists('src'):
        print("Error: This script must be run from the project's root directory.")
        sys.exit(1)

    VerifyApp().run()
