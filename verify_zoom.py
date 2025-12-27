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
        """Directly load the project and trigger UI updates."""
        print("Loading project programmatically...")
        # 1. Load project directly into the sequencer object
        result = self.layout.sequencer.load_project("verification_project")
        print(f"Load result: {result}")

        if "Error" in result:
            print("Failed to load project.")
            self.stop()
            return

        # 2. Manually trigger the UI update that populates track_widgets
        print("Manually triggering UI update...")
        self.layout.update_status_display()

        # 3. Schedule the check for the widget to allow the UI to draw
        Clock.schedule_once(self.check_widget_and_open_editor, 0.5)

    def check_widget_and_open_editor(self, dt):
        """Check if widgets are populated, then open the editor."""
        if not self.layout.track_widgets:
            print("Error: Track widgets not found after manual UI update.")
            self.stop()
            return

        print("Track widget found. Opening editor.")
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
        for i in range(10): # Check for a few possible suffixes
            suffixed_name = f"{base_name}{i:04d}{ext}"
            if os.path.exists(suffixed_name):
                os.rename(suffixed_name, screenshot_path)
                found_file = screenshot_path
                break

        if found_file:
             print(f"Screenshot saved to {screenshot_path}")
        elif os.path.exists(screenshot_path):
             print(f"Screenshot saved to {screenshot_path}")
        else:
             print("Error: Screenshot not found.")

        self.stop()

if __name__ == '__main__':
    VerifyZoomApp().run()
