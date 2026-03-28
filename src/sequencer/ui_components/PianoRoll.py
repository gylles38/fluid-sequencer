from kivy.uix.widget import Widget
from kivy.uix.scrollview import ScrollView
from kivy.properties import NumericProperty, ObjectProperty, ListProperty
from kivy.metrics import dp
from kivy.graphics import Color, Rectangle, Line, Mesh
from kivy.clock import Clock
import bisect
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
    note_height = NumericProperty(dp(12))
    editor = ObjectProperty(None, allownone=True)
    selected_notes = ListProperty([])

    def __init__(self, **kwargs):
        super(PianoRoll, self).__init__(**kwargs)
        self.size_hint = (None, None)
        self.height = 128 * self.note_height
        self._redraw_pending = False
        self._selected_ids_cache = set()

        # Update width when beats or zoom changes
        self.bind(total_beats=self._update_width, pixels_per_beat=self._update_width)
        # Redraw when state or geometry changes (debounced to avoid infinite loops)
        self.bind(track=self.redraw, pos=self.redraw, size=self.redraw, selected_notes=self.redraw)
        self._update_width()
        self.redraw()

    def _update_width(self, *args):
        self.width = self.total_beats * self.pixels_per_beat

    def redraw(self, *args):
        """Debounced redraw of grid and notes."""
        if self._redraw_pending:
            return
        self._redraw_pending = True
        Clock.unschedule(self._do_redraw)
        Clock.schedule_once(self._do_redraw, 0)

    def _do_redraw(self, dt):
        self._redraw_pending = False
        self.draw()

    def _velocity_to_color(self, velocity):
        """Converts MIDI velocity (0-127) to a color for visualization."""
        normalized_velocity = velocity / 127.0
        red = normalized_velocity
        blue = 1.0 - normalized_velocity
        green = 0.3
        return (red, green, blue, 0.9)

    def _get_viewport(self):
        """Calculates the visible viewport based on the parent ScrollView."""
        viewport_x = 0
        viewport_y = 0
        viewport_w = self.width
        viewport_h = self.height

        parent = self.parent
        # Small optimization: cache the scrollview if found
        if hasattr(self, '_scroll_view_cache') and self._scroll_view_cache and self._scroll_view_cache.parent:
            parent = self._scroll_view_cache
        else:
            self._scroll_view_cache = None

        while parent:
            if isinstance(parent, ScrollView):
                self._scroll_view_cache = parent
                viewport_x = parent.scroll_x * max(0, self.width - parent.width)
                viewport_y = parent.scroll_y * max(0, self.height - parent.height)
                viewport_w = parent.width
                viewport_h = parent.height
                break
            parent = parent.parent
        return viewport_x, viewport_y, viewport_w, viewport_h

    def draw(self, *args):
        self.canvas.before.clear()
        self.canvas.clear()

        # Performance Optimization: Use a cached set for O(1) selection lookup.
        # Note: We rebuild it on draw, but we could optimize further by binding to selected_notes change.
        # Given redraw() is debounced, this is acceptable.
        selected_ids = {id(n) for n in self.selected_notes}
        if self.editor and self.editor.selected_note:
            selected_ids.add(id(self.editor.selected_note))

        # Performance Optimization: Calculate visible viewport to skip rendering non-visible notes.
        # This is crucial for long tracks with many notes to prevent UI thread freezes.
        viewport_x, viewport_y, viewport_w, viewport_h = self._get_viewport()

        with self.canvas.before:
            Color(0.1, 0.1, 0.12, 1)
            Rectangle(pos=self.pos, size=self.size)

            # --- Optimized Grid using Mesh ---
            black_keys_vertices = []
            white_keys_vertices = []
            octave_vertices = []

            # Optimization: Only draw visible horizontal grid lines
            start_pitch = max(0, int(viewport_y / self.note_height))
            end_pitch = min(127, int((viewport_y + viewport_h) / self.note_height) + 1)

            x1, x2 = viewport_x, viewport_x + viewport_w
            for i in range(start_pitch, end_pitch + 1):
                note_y = i * self.note_height
                if (i % 12) in [1, 3, 6, 8, 10]:
                    black_keys_vertices.extend([x1, note_y, 0, 0, x2, note_y, 0, 0])
                else:
                    white_keys_vertices.extend([x1, note_y, 0, 0, x2, note_y, 0, 0])

                if (i % 12) == 11:
                    octave_line_y = note_y + self.note_height
                    octave_vertices.extend([x1, octave_line_y, 0, 0, x2, octave_line_y, 0, 0])

            if black_keys_vertices:
                Color(0.15, 0.15, 0.17, 1)
                Mesh(vertices=black_keys_vertices, indices=list(range(len(black_keys_vertices)//4)), mode='lines')
            if white_keys_vertices:
                Color(0.2, 0.2, 0.22, 1)
                Mesh(vertices=white_keys_vertices, indices=list(range(len(white_keys_vertices)//4)), mode='lines')
            if octave_vertices:
                Color(0.8, 0.8, 0.8, 0.6)
                Mesh(vertices=octave_vertices, indices=list(range(len(octave_vertices)//4)), mode='lines')

            # Vertical grid lines
            # Performance Optimization: Only draw visible grid lines
            major_vertices = []
            minor_vertices = []

            start_beat = int(viewport_x / self.pixels_per_beat)
            end_beat = int((viewport_x + viewport_w) / self.pixels_per_beat) + 1
            start_beat = max(0, start_beat)
            end_beat = min(int(self.total_beats), end_beat)

            for i in range(start_beat, end_beat + 1):
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

        # --- Notes ---
        if isinstance(self.track, MidiTrack) and self.track.events:
            with self.canvas:
                # Performance Optimization: Use binary search to find starting events
                # We need events that could reach viewport_x. Since events are sorted by start_time,
                # we search for events starting at or after (viewport_x / pixels_per_beat) - max_note_len.
                # Assuming a safe max note length of 32 beats for clipping.
                search_beat = max(0, (viewport_x / self.pixels_per_beat) - 32)
                start_idx = bisect.bisect_left(self.track.events, search_beat, key=lambda e: e.start_time)

                for i in range(start_idx, len(self.track.events)):
                    event = self.track.events[i]
                    note_x = event.start_time * self.pixels_per_beat

                    # Temporal clipping: stop if we've passed the viewport
                    if note_x > viewport_x + viewport_w:
                        break

                    for note in event.notes:
                        note_y = note.pitch * self.note_height
                        note_width = note.duration * self.pixels_per_beat

                        if note_x + note_width < viewport_x:
                            continue

                        # Pitch clipping: check if the note is vertically within view
                        if note_y + self.note_height < viewport_y or note_y > viewport_y + viewport_h:
                            continue

                        note_color = self._velocity_to_color(note.velocity)

                        # Draw the main note body
                        Color(*note_color)
                        Rectangle(pos=(note_x, note_y), size=(note_width, self.note_height))

                        # Draw resize handles if the note is wide enough
                        if note_width > dp(16):
                            handle_width = min(dp(8), note_width / 4)
                            handle_color = (min(1.0, note_color[0] * 1.2), min(1.0, note_color[1] * 1.2), min(1.0, note_color[2] * 1.2), 1.0)
                            Color(*handle_color)
                            # Left handle
                            Rectangle(pos=(note_x, note_y), size=(handle_width, self.note_height))
                            # Right handle
                            Rectangle(pos=(note_x + note_width - handle_width, note_y), size=(handle_width, self.note_height))

                        # Draw outline for selected note.
                        # Performance Optimization: Use O(1) set lookup
                        if id(note) in selected_ids:
                            Color(1, 1, 1, 1)  # White outline
                            Line(rectangle=(note_x, note_y, note_width, self.note_height), width=1.1)


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