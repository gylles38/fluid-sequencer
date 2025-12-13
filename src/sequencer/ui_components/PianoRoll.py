from kivy.uix.floatlayout import FloatLayout
from kivy.uix.scrollview import ScrollView
from kivy.properties import NumericProperty, ObjectProperty
from kivy.metrics import dp
from kivy.graphics import Color, Rectangle, Line
from sequencer.models import MidiTrack

class PianoRoll(FloatLayout):
    """
    Represents the drawing area of the piano roll's grid and notes.
    This widget is intended to be placed inside a ScrollView.
    """
    total_beats = NumericProperty(128.0)
    pixels_per_beat = NumericProperty(dp(100))
    track = ObjectProperty(None, allownone=True)
    beat_per_measure = NumericProperty(4)
    note_height = NumericProperty(dp(12))

    def __init__(self, **kwargs):
        super(PianoRoll, self).__init__(**kwargs)
        self.size_hint = (None, None)
        self.height = 128 * self.note_height

        self.bind(total_beats=self._update_width, pixels_per_beat=self._update_width,
                  track=self.draw, pos=self.draw, size=self.draw)
        self._update_width()

    def _update_width(self, *args):
        self.width = self.total_beats * self.pixels_per_beat
        self.draw()

    def _velocity_to_color(self, velocity):
        """Converts MIDI velocity (0-127) to a color for visualization."""
        normalized_velocity = velocity / 127.0
        red = normalized_velocity
        blue = 1.0 - normalized_velocity
        green = 0.3
        return (red, green, blue, 0.9)

    def draw(self, *args):
        self.canvas.before.clear()
        self.canvas.clear()

        with self.canvas.before:
            Color(0.1, 0.1, 0.12, 1)
            Rectangle(pos=self.pos, size=self.size)

            # --- Grid ---
            for i in range(128):
                # Y-coordinate is now proportional to pitch (bottom-up)
                note_y = self.y + i * self.note_height
                if (i % 12) in [1, 3, 6, 8, 10]: Color(0.15, 0.15, 0.17, 1) # Black keys
                else: Color(0.2, 0.2, 0.22, 1) # White keys

                # Draw horizontal lines for note separation
                Line(points=[self.x, note_y, self.x + self.width, note_y], width=0.6)

                # Draw thicker lines to mark octaves (after B notes)
                if (i % 12) == 11:
                    Color(0.8, 0.8, 0.8, 0.6)
                    # Draw octave line at the TOP of the B key row, to separate from C
                    octave_line_y = note_y + self.note_height
                    Line(points=[self.x, octave_line_y, self.x + self.width, octave_line_y], width=1.2)

            current_beat = 0
            while current_beat < self.total_beats:
                x_pos = current_beat * self.pixels_per_beat
                if current_beat % self.beat_per_measure == 0:
                    Color(0.8, 0.8, 0.8, 0.8)
                    Line(points=[x_pos, self.y, x_pos, self.y + self.height], width=1.5)
                else:
                    Color(0.5, 0.5, 0.5, 0.4)
                    Line(points=[x_pos, self.y, x_pos, self.y + self.height], width=0.5)
                current_beat += 1

        # --- Notes ---
        if isinstance(self.track, MidiTrack):
            with self.canvas:
                for event in self.track.events:
                    for note in event.notes:
                        note_x = event.start_time * self.pixels_per_beat
                        note_y = self.y + note.pitch * self.note_height
                        note_width = note.duration * self.pixels_per_beat

                        Color(*self._velocity_to_color(note.velocity))
                        Rectangle(pos=(note_x, note_y), size=(note_width, self.note_height))


class PianoRollViewer(ScrollView):
    """
    A scrollable container for the PianoRoll grid widget.
    It handles vertical scrolling for the grid part of the piano roll.
    """
    total_beats = NumericProperty(128.0)
    pixels_per_beat = NumericProperty(dp(100))
    track = ObjectProperty(None, allownone=True)
    note_height = NumericProperty(dp(12))

    def __init__(self, **kwargs):
        super(PianoRollViewer, self).__init__(**kwargs)
        self.size_hint_x = None
        self.do_scroll_x = False
        self.do_scroll_y = True

        self.grid = PianoRoll(
            track=self.track,
            total_beats=self.total_beats,
            pixels_per_beat=self.pixels_per_beat,
            note_height=self.note_height
        )
        self.add_widget(self.grid)

        # Bind this viewer's width to the grid's width ("content-out" sizing)
        self.grid.bind(width=self.setter('width'))

    def on_track(self, instance, value):
        if hasattr(self, 'grid'):
            self.grid.track = value

    def on_total_beats(self, instance, value):
        if hasattr(self, 'grid'):
            self.grid.total_beats = value

    def on_pixels_per_beat(self, instance, value):
        if hasattr(self, 'grid'):
            self.grid.pixels_per_beat = value

    def on_note_height(self, instance, value):
        if hasattr(self, 'grid'):
            self.grid.note_height = value
