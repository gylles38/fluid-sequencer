
import os
import sys

# Add src to PYTHONPATH
sys.path.append(os.path.abspath("src"))

from kivy.config import Config
Config.set('graphics', 'width', '1200')
Config.set('graphics', 'height', '800')

from kivymd.app import MDApp
from kivy.clock import Clock
from sequencer.kivy_ui import SequencerLayout
from sequencer.models import Song, MidiTrack
from kivy.metrics import dp
from kivy.uix.label import Label

class VerifyExtendApp(MDApp):
    def build(self):
        song = Song(name="Extend Project")
        # Project starts with one MIDI track
        # By default it should have some measures.

        from sequencer.sequencer import Sequencer
        self.sequencer = Sequencer()
        self.sequencer.song = song
        # Force initial state
        self.sequencer.ui_end_pos_str = "7:1" # 6 measures

        self.layout = SequencerLayout(sequencer=self.sequencer)

        Clock.schedule_once(self.simulate_extend, 2)
        return self.layout

    def simulate_extend(self, dt):
        print("--- EXTEND SIMULATION ---")
        # 1. Check initial state
        print(f"Initial total_beats: {self.layout.ruler.total_beats}")

        # 2. Simulate user typing "10:1" in End: field
        print("\nExtending to 10:1...")
        self.layout.end_pos_input.text = "10:1"
        self.layout.on_end_position_validate()

        # 3. Wait for layout to update and check alignment
        Clock.schedule_once(self.check_alignment, 1)

    def check_alignment(self, dt):
        ruler = self.layout.ruler
        r_content = ruler.ruler_content
        tw = self.layout.track_widgets[0]
        t_content = tw.timeline_container

        print(f"\nFinal total_beats: {ruler.total_beats}")

        # Check Measure 8 alignment
        label8 = None
        for child in r_content.children:
            if isinstance(child, Label) and child.text == "8":
                label8 = child
                break

        if label8:
            l8_window_x = label8.to_window(0, 0)[0]
            # Measure 8 line is at beat 28
            beat_28_x = t_content.to_window(28 * tw.pixels_per_beat, 0)[0]

            print(f"Measure 8 Label Window X: {l8_window_x}")
            print(f"Track Beat 28 Window X: {beat_28_x}")
            # The label 8 starts at beat 28, but its text is left-aligned with padding.
            # So window X of label should match window X of beat 28.
            print(f"Alignment Error at Measure 8: {l8_window_x - beat_28_x}px")

            # Check Measure 9
            label9 = None
            for child in r_content.children:
                if isinstance(child, Label) and child.text == "9":
                    label9 = child
                    break
            if label9:
                l9_window_x = label9.to_window(0, 0)[0]
                beat_32_x = t_content.to_window(32 * tw.pixels_per_beat, 0)[0]
                print(f"Measure 9 Label Window X: {l9_window_x}")
                print(f"Track Beat 32 Window X: {beat_32_x}")
                print(f"Alignment Error at Measure 9: {l9_window_x - beat_32_x}px")

        # Check scroll_x sync
        print(f"\nRuler scroll_x: {ruler.scroll_view.scroll_x}")
        print(f"Track scroll_x: {tw.timeline_scroll.scroll_x}")

        self.stop()

if __name__ == "__main__":
    VerifyExtendApp().run()
