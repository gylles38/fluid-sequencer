
import os
from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from sequencer.kivy_ui import SequencerApp
from sequencer.models import MidiTrack, AutomationTrack
from kivy.metrics import dp

# Force headless for the test
os.environ['KIVY_NO_ARGS'] = '1'

class TestApp(SequencerApp):
    def on_start(self):
        Clock.schedule_once(self.run_verification, 2)

    def run_verification(self, dt):
        if not self.sequencer_layout.sequencer.song.tracks:
            self.sequencer_layout.sequencer.song.add_track(MidiTrack(name="TestMIDI"))
            self.sequencer_layout.update_status_display()

        tw = self.sequencer_layout.track_widgets[0]
        tw.open_piano_roll_editor()

        self.sequencer_layout.sequencer.song.add_track(AutomationTrack(name="TestAuto", target_track_index=0))
        self.sequencer_layout.update_status_display()

        Clock.schedule_once(self.open_auto_editor, 1)

    def open_auto_editor(self, dt):
        auto_tw = next(tw for tw in self.sequencer_layout.track_widgets if tw.track.name == "TestAuto")
        auto_tw.open_automation_editor()

        wm = self.sequencer_layout.window_manager
        if len(wm.children) >= 2:
            wm.children[0].pos = (dp(50), dp(50))
            wm.children[0].size = (dp(500), dp(400))
            wm.children[1].pos = (dp(400), dp(200))
            wm.children[1].size = (dp(500), dp(400))

        Clock.schedule_once(self.capture, 1)

    def capture(self, dt):
        Window.screenshot('verification_final.png')
        App.get_running_app().stop()

if __name__ == "__main__":
    TestApp().run()
