from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.scrollview import ScrollView
from kivy.properties import NumericProperty, ObjectProperty
from kivy.metrics import dp
from kivy.graphics import Color, Rectangle, Line
from sequencer.models import MidiTrack
from .PianoKeyboard import PianoKeyboard

class PianoRoll(FloatLayout):
    """
    Represents the drawing area of the piano roll's grid and notes.
    """
    total_beats = NumericProperty(128.0)
    pixels_per_beat = NumericProperty(dp(100))
    track = ObjectProperty(None, allownone=True)
    beat_per_measure = NumericProperty(4)
    note_height = NumericProperty(dp(12))

    def __init__(self, **kwargs):
        super(PianoRoll, self).__init__(**kwargs)
        self.size_hint = (None, 1) # Takes full height of parent

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
                note_y = self.y + (127 - i) * self.note_height
                if (i % 12) in [1, 3, 6, 8, 10]: Color(0.15, 0.15, 0.17, 1)
                else: Color(0.2, 0.2, 0.22, 1)
                width = 1.1 if (i % 12) in [4, 11] else 0.6
                Line(points=[self.x, note_y, self.x + self.width, note_y], width=width)

            current_beat = 0
            while current_beat < self.total_beats:
                x_pos = self.x + current_beat * self.pixels_per_beat
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
                        note_x = self.x + event.start_time * self.pixels_per_beat
                        note_y = self.y + (127 - note.pitch) * self.note_height
                        note_width = note.duration * self.pixels_per_beat

                        Color(*self._velocity_to_color(note.velocity))
                        Rectangle(pos=(note_x, note_y), size=(note_width, self.note_height))

class PianoRollContent(BoxLayout):
    """
    Internal container holding the keyboard and the grid, allowing them to scroll together.
    """
    track = ObjectProperty(None, allownone=True)
    total_beats = NumericProperty(128.0)
    pixels_per_beat = NumericProperty(dp(100))
    note_height = NumericProperty(dp(12))

    def __init__(self, **kwargs):
        super(PianoRollContent, self).__init__(**kwargs)
        self.size_hint = (None, None)
        self.height = 128 * self.note_height

        self.keyboard = PianoKeyboard(note_height=self.note_height)
        self.grid = PianoRoll(
            track=self.track,
            total_beats=self.total_beats,
            pixels_per_beat=self.pixels_per_beat,
            note_height=self.note_height
        )
        self.add_widget(self.keyboard)
        self.add_widget(self.grid)
        self._update_width()

    def on_track(self, instance, value):
        if hasattr(self, 'grid'):
            self.grid.track = value

    def on_total_beats(self, instance, value):
        if hasattr(self, 'grid'):
            self.grid.total_beats = value
            self._update_width()

    def on_pixels_per_beat(self, instance, value):
        if hasattr(self, 'grid'):
            self.grid.pixels_per_beat = value
            self._update_width()

    def _update_width(self, *args):
        if hasattr(self, 'keyboard') and hasattr(self, 'grid'):
            self.width = self.keyboard.width + self.grid.width

class PianoRollViewer(ScrollView):
    """
    The final, user-facing widget. A scrollable container for the piano roll content.
    """
    total_beats = NumericProperty(128.0)
    pixels_per_beat = NumericProperty(dp(100))
    track = ObjectProperty(None, allownone=True)
    note_height = NumericProperty(dp(12))

    def __init__(self, **kwargs):
        super(PianoRollViewer, self).__init__(**kwargs)
        self.size_hint = (None, 1) # Critical for horizontal scrolling parent
        self.do_scroll_x = False
        self.do_scroll_y = True

        self.content = PianoRollContent(
            track=self.track,
            total_beats=self.total_beats,
            pixels_per_beat=self.pixels_per_beat,
            note_height=self.note_height
        )
        self.add_widget(self.content)
        self._update_width()

    def on_track(self, instance, value):
        if hasattr(self, 'content'):
            self.content.track = value

    def on_total_beats(self, instance, value):
        if hasattr(self, 'content'):
            self.content.total_beats = value
            self._update_width()

    def on_pixels_per_beat(self, instance, value):
        if hasattr(self, 'content'):
            self.content.pixels_per_beat = value
            self._update_width()

    def _update_width(self, *args):
        if hasattr(self, 'content'):
            self.width = self.content.width
