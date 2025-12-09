from kivy.uix.floatlayout import FloatLayout
from kivy.graphics import Color, Rectangle
from kivy.uix.label import Label
from kivy.properties import ObjectProperty, NumericProperty

class PianoRoll(FloatLayout):
    track = ObjectProperty(None)
    pixels_per_beat = NumericProperty(100)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(track=self.update_notes, pixels_per_beat=self.update_notes)

    def update_notes(self, *args):
        self.canvas.clear()
        if self.track:
            with self.canvas:
                for event in self.track.events:
                    for note in event.notes:
                        self._draw_note(note, event.start_time)

    def _draw_note(self, note, start_time):
        # Constants for drawing
        NOTE_HEIGHT = 10
        MAX_VELOCITY = 127.0

        # Velocity to color (blue tint)
        velocity_normalized = note.velocity / MAX_VELOCITY
        color = (0.2, 0.5, 1.0, velocity_normalized)  # RGBA

        # Position and size
        x = start_time * self.pixels_per_beat
        y = note.pitch * NOTE_HEIGHT
        width = note.duration * self.pixels_per_beat

        Color(*color)
        Rectangle(pos=(x, y), size=(width, NOTE_HEIGHT))

        # Add note name
        note_names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
        note_name = note_names[note.pitch % 12]

        label = Label(
            text=note_name,
            pos=(x, y),
            size=(width, NOTE_HEIGHT),
            font_size='9sp',
            halign='center',
            valign='middle'
        )
        label.texture_update()
        Color(1, 1, 1, 1) # White text
        Rectangle(texture=label.texture, pos=label.pos, size=label.size)
