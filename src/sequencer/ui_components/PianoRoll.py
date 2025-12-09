from kivy.uix.scrollview import ScrollView
from kivy.uix.floatlayout import FloatLayout
from kivy.graphics import Color, Rectangle
from kivy.uix.label import Label
from kivy.properties import ObjectProperty, NumericProperty
from kivy.metrics import dp

class PianoRoll(ScrollView):
    track = ObjectProperty(None)
    pixels_per_beat = NumericProperty(100)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.do_scroll_x = False
        self.do_scroll_y = True

        self.content = FloatLayout(size_hint=(None, None))
        self.content.height = dp(1280)
        self.add_widget(self.content)

        self.bind(track=self.update_notes, pixels_per_beat=self.update_notes)
        self.content.bind(size=self.update_notes)

    def set_content_width(self, width):
        self.content.width = width

    def update_notes(self, *args):
        self.content.canvas.clear()
        if self.track:
            with self.content.canvas:
                for event in self.track.events:
                    for note in event.notes:
                        self._draw_note(note, event.start_time)

    def _draw_note(self, note, start_time):
        note_height = 10
        MAX_VELOCITY = 127.0

        velocity_normalized = note.velocity / MAX_VELOCITY
        color = (0.2, 0.5, 1.0, velocity_normalized)

        x = start_time * self.pixels_per_beat
        y = note.pitch * note_height
        width = note.duration * self.pixels_per_beat

        Color(*color)
        Rectangle(pos=(x, y), size=(width, note_height))

        note_names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
        note_name = note_names[note.pitch % 12]

        label = Label(
            text=note_name,
            pos=(x, y),
            size=(width, note_height),
            font_size='8sp',
            halign='center',
            valign='middle'
        )
        label.texture_update()
        Color(1, 1, 1, 1)
        Rectangle(texture=label.texture, pos=label.pos, size=label.size)
