
import os
import time
from kivy.config import Config
Config.set('graphics', 'width', '1024')
Config.set('graphics', 'height', '768')
from kivymd.app import MDApp
from kivy.clock import Clock
from sequencer.kivy_ui import SequencerLayout
from sequencer.sequencer import Sequencer
from sequencer.models import MidiTrack

class ScreenshotApp(MDApp):
    def build(self):
        self.sequencer = Sequencer(gui_mode=True)
        # Add a couple of tracks
        self.sequencer.add_track("Lead Synth", "midi")
        self.sequencer.add_track("Bass Synth", "midi")

        # Arm the first track to show the bridge routing
        self.sequencer.set_record_mode(0, "KEEP")

        self.layout = SequencerLayout(sequencer=self.sequencer)
        return self.layout

    def on_start(self):
        Clock.schedule_once(self.take_screenshot, 2)

    def take_screenshot(self, dt):
        filename = "screenshots/bridge_verification.png"
        os.makedirs("screenshots", exist_ok=True)
        self.layout.export_to_png(filename)
        print(f"Screenshot saved to {filename}")
        self.stop()

if __name__ == "__main__":
    ScreenshotApp().run()
