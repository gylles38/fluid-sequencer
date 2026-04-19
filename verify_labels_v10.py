
import os
os.environ['KIVY_NO_ARGS'] = '1'
os.environ['Kivy_NO_CONSOLELOG'] = '1'
os.environ['KIVY_BACKEND'] = 'sdl2'

import kivy
from kivymd.app import MDApp
from kivy.clock import Clock
from kivy.metrics import dp
from sequencer.ui_components.piano_roll_editor import PianoRollEditor, EditorPianoKeyboard
from sequencer.models import MidiTrack, Song
from kivy.uix.label import Label
from kivy.core.window import Window

class MockSequencer:
    def __init__(self):
        self.song = Song(name="Test Song")
        self.playback_state = "stopped"
        self.current_beat = 0
        self.ui_start_pos_str = "1:1"
        self.ui_end_pos_str = "1:1"
        self.track_overrides = {}
        self.tempo = 120
    def bind(self, **kwargs): pass
    def unbind(self, **kwargs): pass
    def get_song_length_in_beats(self): return 128
    def _format_beats_to_position(self, beat): return f"{int(beat//4)+1}:{int(beat%4)+1}"
    def parse_position_to_beats(self, pos): return 0.0

class MockSequencerLayout:
    def __init__(self, seq):
        self.sequencer = seq
        self.start_pos_input = type('obj', (object,), {'text': ''})
        self.end_pos_input = type('obj', (object,), {'text': ''})
        self.track_widgets = []

class VerifyApp(MDApp):
    def build(self):
        seq = MockSequencer()
        track = MidiTrack(name="Test Track")
        seq.song.tracks.append(track)
        sl = MockSequencerLayout(seq)
        self.editor = PianoRollEditor(track=track, sequencer_layout=sl)
        return self.editor

    def on_start(self):
        Clock.schedule_once(self.check_labels, 1.0)

    def check_labels(self, dt):
        kb = self.editor.ids.piano_keyboard
        labels = [c for c in kb.children if isinstance(c, Label)]
        print(f"Total labels: {len(labels)}")
        for i, l in enumerate(labels):
            print(f"Label {i}: text='{l.text}', pos={l.pos}, size={l.size}, opacity={l.opacity}, color={l.color}")
        self.stop()

if __name__ == "__main__":
    Window.size = (1024, 768)
    VerifyApp().run()
