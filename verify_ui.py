
import os
import sys
# Add the 'src' directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from kivy.clock import Clock
from kivymd.app import MDApp
from kivy.core.window import Window
from sequencer.kivy_ui import SequencerLayout
import time

class UiVerificationApp(MDApp):
    def build(self):
        # Set the window size for consistency
        Window.size = (1200, 800)
        # Load the layout. The project file will be loaded by the SequencerApp's on_start logic.
        return SequencerLayout()

    def on_start(self):
        # The UI needs a moment to fully initialize and load the project.
        # We'll schedule the screenshot for a short time in the future.
        Clock.schedule_once(self.take_screenshot, 2)

    def take_screenshot(self, dt):
        print("Taking screenshot...")
        # Ensure the screenshots directory exists
        if not os.path.exists("screenshots"):
            os.makedirs("screenshots")

        # Capture the window
        screenshot_path = "screenshots/verification.png"
        Window.screenshot(name=screenshot_path)

        # After taking the screenshot, stop the app
        print(f"Screenshot saved. Exiting.")
        self.stop()

if __name__ == '__main__':
    # Add project file to sys.argv to be loaded by the app
    sys.argv.extend(['--', 'projects/test_project.proj.json'])
    UiVerificationApp().run()
