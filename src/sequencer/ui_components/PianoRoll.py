from kivy.uix.widget import Widget
from kivy.uix.scrollview import ScrollView
from kivy.properties import NumericProperty, ObjectProperty, ListProperty
from kivy.metrics import dp
from kivy.graphics import Color, Rectangle, Line, Mesh, PushMatrix, PopMatrix, Translate
from kivy.clock import Clock
from sequencer.models import MidiTrack

class PianoRoll(Widget):
    """
    Represents the drawing area of the piano roll's grid and notes.
    This widget is intended to be placed inside a ScrollView.
    """
    total_beats = NumericProperty(128.0)
    pixels_per_beat = NumericProperty(dp(100))
    track = ObjectProperty(None, allownone=True)
    beat_per_measure = NumericProperty(4)
    note_height = NumericProperty(round(dp(14)))
    editor = ObjectProperty(None, allownone=True)
    selected_notes = ListProperty([])

    def on_note_height(self, instance, value):
        self.height = round(128 * value)

    def __init__(self, **kwargs):
        super(PianoRoll, self).__init__(**kwargs)
        self.size_hint = (None, None)
        self.height = round(128 * self.note_height)

        self.bind(total_beats=self.redraw, pixels_per_beat=self.redraw,
                  track=self.redraw, pos=self.redraw, size=self.redraw,
                  note_height=self.redraw)
        self.redraw()

    def redraw(self, *args):
        """Debounced redraw of grid and notes."""
        self.width = self.total_beats * self.pixels_per_beat
        Clock.unschedule(self.draw)
        Clock.schedule_once(self.draw, 0)

    def _velocity_to_color(self, velocity):
        """Converts MIDI velocity (0-127) to a color for visualization."""
        normalized_velocity = velocity / 127.0
        red = normalized_velocity
        blue = 1.0 - normalized_velocity
        green = 0.3
        return (red, green, blue, 0.9)

    def draw(self, *args):
        # Safety check for widget size
        if self.width <= 1 or self.height <= 1:
            return

        self.canvas.before.clear()
        self.canvas.clear()

        with self.canvas.before:
            PushMatrix()
            # USE ABSOLUTE POSITIONS because we use canvas.clear() on a Widget.
            # PianoRoll inherits from Widget, so (0,0) on canvas is window origin.
            Translate(self.x, self.y)

            Color(0.1, 0.1, 0.12, 1)
            Rectangle(pos=(0, 0), size=self.size)

            # --- Optimized Grid using Mesh ---
            black_keys_vertices = []
            white_keys_vertices = []
            octave_vertices = []
            pitch_separator_vertices = []

            for i in range(128):
                y_start = round(i * self.note_height)
                y_end = round((i + 1) * self.note_height)

                # White/Black background rectangles
                if (i % 12) in [1, 3, 6, 8, 10]:
                    black_keys_vertices.append((y_start, y_end))
                else:
                    white_keys_vertices.append((y_start, y_end))

            # Draw backgrounds first
            for y_start, y_end in white_keys_vertices:
                Color(0.18, 0.18, 0.20, 1)
                Rectangle(pos=(0, y_start), size=(self.width, y_end - y_start))
            for y_start, y_end in black_keys_vertices:
                Color(0.14, 0.14, 0.16, 1)
                Rectangle(pos=(0, y_start), size=(self.width, y_end - y_start))

            # Pitch separators (lines)
            for i in range(1, 129):
                y_pos = round(i * self.note_height)
                if (i % 12) == 0:
                    octave_vertices.extend([0, y_pos, 0, 0, self.width, y_pos, 0, 0])
                else:
                    pitch_separator_vertices.extend([0, y_pos, 0, 0, self.width, y_pos, 0, 0])

            if pitch_separator_vertices:
                Color(0.2, 0.2, 0.22, 1)
                Mesh(vertices=pitch_separator_vertices, indices=list(range(len(pitch_separator_vertices)//4)), mode='lines')
            if octave_vertices:
                Color(0.4, 0.4, 0.4, 0.8)
                Mesh(vertices=octave_vertices, indices=list(range(len(octave_vertices)//4)), mode='lines')

            # Vertical grid lines
            major_vertices = []
            minor_vertices = []
            for i in range(int(self.total_beats) + 1):
                x_pos = i * self.pixels_per_beat
                if i % self.beat_per_measure == 0:
                    major_vertices.extend([x_pos, 0, 0, 0, x_pos, self.height, 0, 0])
                else:
                    minor_vertices.extend([x_pos, 0, 0, 0, x_pos, self.height, 0, 0])

            if major_vertices:
                Color(0.8, 0.8, 0.8, 0.8)
                Mesh(vertices=major_vertices, indices=list(range(len(major_vertices)//4)), mode='lines')
            if minor_vertices:
                Color(0.5, 0.5, 0.5, 0.4)
                Mesh(vertices=minor_vertices, indices=list(range(len(minor_vertices)//4)), mode='lines')

            PopMatrix()

        # --- Notes ---
        if isinstance(self.track, MidiTrack):
            with self.canvas:
                PushMatrix()
                Translate(self.x, self.y)

                for event in self.track.events:
                    for note in event.notes:
                        note_x = event.start_time * self.pixels_per_beat
                        y_start = round(note.pitch * self.note_height)
                        y_end = round((note.pitch + 1) * self.note_height)
                        note_h = y_end - y_start

                        note_width = note.duration * self.pixels_per_beat
                        note_color = self._velocity_to_color(note.velocity)

                        # Draw the main note body
                        Color(*note_color)
                        Rectangle(pos=(note_x, y_start), size=(note_width, note_h))

                        # Draw resize handles if the note is wide enough
                        if note_width > dp(16):
                            handle_width = min(dp(8), note_width / 4)
                            handle_color = (min(1.0, note_color[0] * 1.2), min(1.0, note_color[1] * 1.2), min(1.0, note_color[2] * 1.2), 1.0)
                            Color(*handle_color)
                            # Left handle
                            Rectangle(pos=(note_x, y_start), size=(handle_width, note_h))
                            # Right handle
                            Rectangle(pos=(note_x + note_width - handle_width, y_start), size=(handle_width, note_h))

                        # Draw outline for selected note.
                        # Both the legacy `selected_note` and the new `selected_notes` list must be
                        # checked using identity (`is`) to handle identical-looking but distinct note objects.
                        is_in_multi_select = any(note is sel_note for sel_note in self.selected_notes)
                        is_the_single_select = self.editor and self.editor.selected_note is note

                        if is_in_multi_select or is_the_single_select:
                            Color(1, 1, 1, 1)  # White outline
                            Line(rectangle=(note_x, y_start, note_width, note_h), width=1.1)

                PopMatrix()


class PianoRollViewer(ScrollView):
    """
    A scrollable container for the PianoRoll grid widget.
    It handles vertical scrolling for the grid part of the piano roll.
    """
    total_beats = NumericProperty(128.0)
    pixels_per_beat = NumericProperty(dp(100))
    track = ObjectProperty(None, allownone=True)
    note_height = NumericProperty(round(dp(14)))

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