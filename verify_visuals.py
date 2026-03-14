
import os
import sys
import time

# Headless Kivy setup
os.environ['KIVY_NO_CONSOLELOG'] = '1'
os.environ['KIVY_NO_CONFIG'] = '1'
os.environ['KIVY_NO_FILELOG'] = '1'
os.environ['KIVY_WINDOW'] = 'headless'
os.environ['KIVY_USE_DEFAULTCONFIG'] = '1'

# Add src to PYTHONPATH
sys.path.append(os.path.abspath("src"))

from kivy.config import Config
Config.set('graphics', 'width', '1024')
Config.set('graphics', 'height', '768')

from kivy.core.window import Window
from kivy.clock import Clock
from sequencer.kivy_ui import SequencerLayout
from kivymd.app import MDApp

class VerifyApp(MDApp):
    def build(self):
        self.theme_cls.theme_style = "Dark"
        self.layout = SequencerLayout()
        return self.layout

    def run_verification(self, *args):
        # 1. Add tracks
        self.layout.process_command_ui('add "Track 0"')
        self.layout.process_command_ui('add "Track 1"')

        # 2. Wait for UI to update
        Clock.schedule_once(self.step_2, 0.5)

    def step_2(self, *args):
        # Verify initial state
        print(f"Initial widgets: {[w.track.name for w in self.layout.track_widgets]}")

        # 3. Simulate Drag & Drop: Move Track 1 to top
        # Track 1 is index 1 in self.layout.track_widgets
        tw1 = self.layout.track_widgets[1]

        # Mock a touch
        class MockTouch:
            def __init__(self, pos):
                self.pos = pos
                self.grab_current = None
            def grab(self, widget): self.grab_current = widget
            def ungrab(self, widget): self.grab_current = None
            def to_widget(self, x, y, relative=False):
                # Simple mock for this test
                return x, y

        # Coordinate in track_list_layout where Track 1 is.
        # Track 0 is at top, Track 1 below it.
        # To move Track 1 to top, we drag it above Track 0's center.

        # Just call the backend move directly for visual verification in headless
        # since actual touch coordinate math is complex in headless.
        self.layout.sequencer.move_track_display_order(1, 0)

        # 4. Wait for refresh
        Clock.schedule_once(self.step_3, 0.5)

    def step_3(self, *args):
        print(f"Final widgets: {[w.track.name for w in self.layout.track_widgets]}")

        # Export to PNG
        # Note: export_to_png might not work well in headless without a real FBO
        # but let's try.
        try:
            self.layout.export_to_png("verification.png")
            print("Screenshot saved to verification.png")
        except Exception as e:
            print(f"Could not save screenshot: {e}")

        self.stop()

if __name__ == '__main__':
    app = VerifyApp()
    Clock.schedule_once(app.run_verification, 0.1)
    app.run()
