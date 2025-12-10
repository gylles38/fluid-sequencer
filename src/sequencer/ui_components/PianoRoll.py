from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget
from kivy.properties import NumericProperty, ObjectProperty
from kivy.graphics import Color, Rectangle
from kivy.metrics import dp
from kivy.uix.floatlayout import FloatLayout

class PianoRoll(ScrollView):
    timeline_width = NumericProperty(0)
    pixels_per_beat = NumericProperty(0)
    track = ObjectProperty(None)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.note_container = FloatLayout(size_hint=(None, None))
        self.add_widget(self.note_container)
        self.bind(pixels_per_beat=self.draw_notes, track=self.draw_notes, timeline_width=self.draw_notes)

    def draw_notes(self, *args):
        if not self.track or not self.pixels_per_beat or self.timeline_width == 0:
            return

        self.note_container.canvas.clear()
        self.note_container.width = self.timeline_width
        self.note_container.height = 128 * dp(10)

        with self.note_container.canvas:
            for note in self.track.notes:
                note_y = note.pitch * dp(10)
                note_x = note.start * self.pixels_per_beat

                note_width = note.duration * self.pixels_per_beat
                note_height = dp(10)

                velocity_alpha = 0.5 + (note.velocity / 127) * 0.5
                note_color = (0.0, 0.7, 0.9, velocity_alpha)

                Color(*note_color)
                Rectangle(pos=(note_x, note_y), size=(note_width, note_height))
