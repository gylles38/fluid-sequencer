import sys
import os
import kivy
kivy.require('2.3.1')
from kivy.clock import Clock
from kivymd.app import MDApp
from sequencer.kivy_ui import SequencerLayout
from kivy.core.window import Window
import time

class VerificationApp(MDApp):
    def build(self):
        project_file = os.path.abspath('verification_project.proj.json')
        sys.argv.append(project_file)

        self.layout = SequencerLayout()
        return self.layout

    def on_start(self):
        Clock.schedule_once(self.run_verification, 1) # Start verification after 1s

    def run_verification(self, dt):
        print("--- Running Verification Script ---")

        # Check if track widgets have been loaded. If not, reschedule.
        if not self.layout.track_widgets:
            print("Track widgets not loaded yet, retrying in 0.5s...")
            Clock.schedule_once(self.run_verification, 0.5)
            return

        try:
            track_widget = self.layout.track_widgets[0]
            open_button = track_widget.piano_roll_button

            print("Opening piano roll editor...")
            open_button.dispatch('on_press')

            Clock.schedule_once(self.take_screenshot, 2)

        except Exception as e:
            print(f"Error during verification: {e}")
            self.stop()

    def take_screenshot(self, dt):
        print("Taking screenshot...")
        # Kivy's screenshot function can sometimes be unreliable with naming.
        # We'll save it and then find the actual file.
        screenshot_dir = os.path.abspath('.')
        Window.screenshot(name='verification.png')

        # Allow time for the file to be written to disk
        time.sleep(1)

        # Find the actual screenshot file, as Kivy might add a number suffix
        actual_file = None
        for f in os.listdir(screenshot_dir):
            if f.startswith('verification') and f.endswith('.png'):
                actual_file = f
                break

        if actual_file:
            print(f"Screenshot saved as {actual_file}")
        else:
            print("Error: Screenshot file not found.")

        self.stop()

if __name__ == '__main__':
    sys.path.insert(0, os.path.abspath('src'))
    VerificationApp().run()
