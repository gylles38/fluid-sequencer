from kivy.uix.widget import Widget
from kivy.uix.scrollview import ScrollView
from kivy.properties import NumericProperty, ObjectProperty, ListProperty
from kivy.metrics import dp
from kivy.clock import Clock
from kivy.graphics import Color, Rectangle, Line, Mesh
from sequencer.models import MidiTrack

class PianoRoll(Widget):
    """
    Represents the drawing area of the piano roll's grid and notes.
    """
    total_beats = NumericProperty(128.0)
    pixels_per_beat = NumericProperty(dp(100))
    track = ObjectProperty(None, allownone=True)
    beat_per_measure = NumericProperty(4)
    note_height = NumericProperty(round(dp(14)))
    editor = ObjectProperty(None, allownone=True)
    selected_notes = ListProperty([])

    def __init__(self, **kwargs):
        super(PianoRoll, self).__init__(**kwargs)
        self.size_hint = (None, None)
        self.height = 128 * self.note_height

        self.bind(total_beats=self.redraw, pixels_per_beat=self.redraw, note_height=self.redraw,
                  track=self.redraw, pos=self.redraw, size=self.redraw)
        self.redraw()

    def redraw(self, *args):
        """Debounced redraw of grid and notes."""
        self.width = self.total_beats * self.pixels_per_beat
        # Ensure height is exactly the same as the keyboard
        self.height = round(128 * self.note_height)
        # Ensure children widgets are updated if any
        for child in self.children:
             if child.size_hint_y == 1:
                  child.height = self.height
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
        self.canvas.clear()

        with self.canvas:
            # Main background
            Color(0.1, 0.1, 0.12, 1)
            Rectangle(pos=self.pos, size=self.size)

            # --- Row backgrounds for black keys ---
            # Using a slightly different shade to distinguish from the main background
            Color(0.14, 0.14, 0.16, 1)
            for i in range(128):
                if (i % 12) in [1, 3, 6, 8, 10]:
                    y_start = round(i * self.note_height)
                    y_end = round((i + 1) * self.note_height)
                    Rectangle(pos=(self.x, self.y + y_start), size=(self.width, y_end - y_start))

            # --- Horizontal Grid Lines using Mesh ---
            black_keys_vertices = []
            white_keys_vertices = []
            octave_vertices = []

            for i in range(129):
                line_y = round(i * self.note_height)
                # Octave line (C)
                if (i % 12) == 0:
                    octave_vertices.extend([self.x, self.y + line_y, 0, 0, self.x + self.width, self.y + line_y, 0, 0])
                # Line between E and F
                elif (i % 12) == 5:
                    white_keys_vertices.extend([self.x, self.y + line_y, 0, 0, self.x + self.width, self.y + line_y, 0, 0])
                else:
                    black_keys_vertices.extend([self.x, self.y + line_y, 0, 0, self.x + self.width, self.y + line_y, 0, 0])

            if black_keys_vertices:
                Color(0.12, 0.12, 0.14, 1) # Subtler lines
                Mesh(vertices=black_keys_vertices, indices=list(range(len(black_keys_vertices)//4)), mode='lines')
            if white_keys_vertices:
                Color(0.18, 0.18, 0.20, 1)
                Mesh(vertices=white_keys_vertices, indices=list(range(len(white_keys_vertices)//4)), mode='lines')
            if octave_vertices:
                Color(0.4, 0.4, 0.45, 0.8) # Stronger octave/C lines
                Mesh(vertices=octave_vertices, indices=list(range(len(octave_vertices)//4)), mode='lines')

            # Vertical grid lines
            major_vertices = []
            minor_vertices = []
            for i in range(int(self.total_beats) + 1):
                x_pos = round(i * self.pixels_per_beat)
                if i % self.beat_per_measure == 0:
                    major_vertices.extend([self.x + x_pos, self.y, 0, 0, self.x + x_pos, self.y + self.height, 0, 0])
                else:
                    minor_vertices.extend([self.x + x_pos, self.y, 0, 0, self.x + x_pos, self.y + self.height, 0, 0])

            if major_vertices:
                Color(0.8, 0.8, 0.8, 0.8)
                Mesh(vertices=major_vertices, indices=list(range(len(major_vertices)//4)), mode='lines')
            if minor_vertices:
                Color(0.5, 0.5, 0.5, 0.4)
                Mesh(vertices=minor_vertices, indices=list(range(len(minor_vertices)//4)), mode='lines')

        # --- Notes ---
        if isinstance(self.track, MidiTrack):
            # Performance Fix: Pre-calculate selected note IDs for fast lookup
            # This avoids O(N*S) complexity in the loop below.
            selected_ids = {id(n) for n in self.selected_notes}
            single_selected_id = id(self.editor.selected_note) if self.editor and self.editor.selected_note else None

            with self.canvas:
                for event in self.track.events:
                    for note in event.notes:
                        x_start = round(event.start_time * self.pixels_per_beat)
                        x_end = round((event.start_time + note.duration) * self.pixels_per_beat)
                        y_start = round(note.pitch * self.note_height)
                        y_end = round((note.pitch + 1) * self.note_height)

                        note_x = self.x + x_start
                        note_y = self.y + y_start
                        note_width = x_end - x_start
                        note_h = y_end - y_start

                        note_color = self._velocity_to_color(note.velocity)

                        # Draw the main note body
                        Color(*note_color)
                        Rectangle(pos=(note_x, note_y), size=(note_width, note_h))

                        # Draw resize handles if the note is wide enough
                        if note_width > dp(16):
                            handle_width = min(dp(8), note_width / 4)
                            handle_color = (min(1.0, note_color[0] * 1.2), min(1.0, note_color[1] * 1.2), min(1.0, note_color[2] * 1.2), 1.0)
                            Color(*handle_color)
                            # Left handle
                            Rectangle(pos=(note_x, note_y), size=(handle_width, note_h))
                            # Right handle
                            Rectangle(pos=(note_x + note_width - handle_width, note_y), size=(handle_width, note_h))

                        # Draw outline for selected note.
                        note_id = id(note)
                        is_selected = note_id in selected_ids or note_id == single_selected_id

                        if is_selected:
                            Color(1, 1, 1, 1)  # White outline
                            Line(rectangle=(note_x, note_y, note_width, note_h), width=1.1)


class PianoRollViewer(ScrollView):
    """
    A scrollable container for the PianoRoll grid widget.
    It handles vertical scrolling for the grid part of the piano roll.
    """
    total_beats = NumericProperty(128.0)
    pixels_per_beat = NumericProperty(dp(100))
    track = ObjectProperty(None, allownone=True)
    note_height = NumericProperty(dp(14))

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
