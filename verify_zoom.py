import kivy
kivy.require('2.3.1')

from kivymd.app import MDApp
from kivy.clock import Clock
from kivy.core.window import Window
from sequencer.ui_components.piano_roll_editor import PianoRollEditor
from sequencer.kivy_ui import SequencerLayout
import os
import time

class VerifyZoomApp(MDApp):
    def build(self):
        self.layout = SequencerLayout()
        # Disable the initial MIDI port selection popup to avoid blocking the script
        self.layout.sequencer.default_record_port = "dummy"
        Clock.schedule_once(self.setup_and_run_test, 0.5)
        return self.layout

    def setup_and_run_test(self, dt):
        """Directly load the project and then poll for UI updates."""
        print("Loading project programmatically...")
        result = self.layout.sequencer.load_project("verification_project")
        print(f"Load result: {result}")

        if "Error" in result:
            print("Failed to load project.")
            self.stop()
            return

        print("Manually triggering UI update...")
        self.layout.update_status_display()

        # Start polling to check when the track widget appears
        self.start_time = time.time()
        Clock.schedule_once(self.wait_for_track_widget, 0.2)

    def wait_for_track_widget(self, dt):
        """Polls the UI to see if the track widgets have been created."""
        if time.time() - self.start_time > 10.0: # 10 second timeout
            print("Error: Timed out waiting for track widgets to appear.")
            self.stop()
            return

        if self.layout.track_widgets:
            print("Track widget found. Opening editor.")
            self.open_editor()
        else:
            print("Waiting for track widget...")
            Clock.schedule_once(self.wait_for_track_widget, 0.2)

    def open_editor(self, *args):
        track = self.layout.sequencer.song.tracks[0]
        self.editor = PianoRollEditor(sequencer_layout=self.layout, track=track)
        self.editor.open()
        Clock.schedule_once(self.test_zoom, 1)

    def test_zoom(self, dt):
        print("Testing zoom...")
        print("Initial pixels_per_beat:", self.editor.pixels_per_beat)
        self.editor.zoom_in()
        self.editor.zoom_in()
        print("After zoom in:", self.editor.pixels_per_beat)
        Clock.schedule_once(self.take_screenshot_and_exit, 1)

    def take_screenshot_and_exit(self, dt):
        print("Taking screenshot...")
        screenshot_path = 'verification_screenshot.png'
        if os.path.exists(screenshot_path):
            os.remove(screenshot_path)
        Window.screenshot(name=screenshot_path)

        # Kivy often saves as screenshot0001.png, so we find and rename it
        found_file = None
        base_name, ext = os.path.splitext(screenshot_path)
        for i in range(10):
            suffixed_name = f"{base_name}{i:04d}{ext}"
            if os.path.exists(suffixed_name):
                os.rename(suffixed_name, screenshot_path)
                found_file = screenshot_path
                break

        if found_file or os.path.exists(screenshot_path):
             print(f"Screenshot saved to {screenshot_path}")
        else:
             print("Error: Screenshot not found.")
        self.stop()

if __name__ == '__main__':
    VerifyZoomApp().run()
