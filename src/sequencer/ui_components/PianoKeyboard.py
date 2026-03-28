from kivy.uix.widget import Widget
from kivy.properties import NumericProperty
from kivy.graphics import Color, Rectangle, Line
from kivy.uix.label import Label
from kivy.metrics import dp
from kivy.clock import Clock

class PianoKeyboard(Widget):
    """
    A widget that draws a vertical piano keyboard.
    """
    note_height = NumericProperty(round(dp(14)))
    highlighted_note = NumericProperty(-1)

    def on_note_height(self, instance, value):
        self.height = round(128 * value)

    def __init__(self, **kwargs):
        super(PianoKeyboard, self).__init__(**kwargs)
        self.size_hint = (None, None)
        self.height = round(128 * self.note_height)
        self.width = dp(40)
        self._texture_cache = {}

        self.bind(pos=self._redraw_on_schedule, size=self._redraw_on_schedule, note_height=self._redraw_on_schedule,
                  highlighted_note=self._redraw_on_schedule)
        self._redraw_on_schedule()

    def get_note_texture(self, text):
        from kivy.core.text import Label as CoreLabel
        if text not in self._texture_cache:
            lbl = CoreLabel(text=text, font_size=dp(9), color=(0, 0, 0, 1))
            lbl.refresh()
            self._texture_cache[text] = lbl.texture
        return self._texture_cache[text]

    def _redraw_on_schedule(self, *args):
        # Schedule the redraw for the next frame to ensure all properties are updated.
        Clock.unschedule(self._redraw)
        Clock.schedule_once(self._redraw)

    def _redraw(self, *args):
        self.canvas.clear()
        # Optimization: Use canvas labels instead of widgets for performance

        # Optimization: Calculate viewport to only draw visible keys
        viewport_y = 0
        viewport_h = self.height
        from kivy.uix.scrollview import ScrollView
        parent = self.parent
        while parent:
            if isinstance(parent, ScrollView):
                if not hasattr(self, '_sv_bound'):
                    parent.bind(scroll_y=self._redraw_on_schedule)
                    self._sv_bound = True
                viewport_y = parent.scroll_y * max(0, self.height - parent.height)
                viewport_h = parent.height
                break
            parent = parent.parent

        start_idx = max(0, int(viewport_y / self.note_height))
        end_idx = min(127, int((viewport_y + viewport_h) / self.note_height) + 1)

        highlight_color = (0.3, 0.7, 1.0, 1) # A light blue color for highlighting

        with self.canvas:
            # --- Draw White Keys Backgrounds ---
            for i in range(start_idx, end_idx + 1):
                if (i % 12) not in [1, 3, 6, 8, 10]:
                    if i == self.highlighted_note:
                        Color(*highlight_color)
                    else:
                        Color(0.95, 0.95, 0.95, 1)
                    y_start = round(i * self.note_height)
                    y_end = round((i + 1) * self.note_height)
                    Rectangle(pos=(self.x, self.y + y_start), size=(self.width, y_end - y_start))

            # --- Draw Black Keys Backgrounds ---
            for i in range(start_idx, end_idx + 1):
                if (i % 12) in [1, 3, 6, 8, 10]:
                    if i == self.highlighted_note:
                        Color(*highlight_color)
                    else:
                        Color(0.1, 0.1, 0.1, 1)
                    y_start = round(i * self.note_height)
                    y_end = round((i + 1) * self.note_height)
                    Rectangle(pos=(self.x, self.y + y_start), size=(self.width * 0.65, y_end - y_start))

            # --- Draw EVERY Pitch Separator (Grid sync) ---
            for i in range(start_idx + 1, end_idx + 2):
                if i > 128: break
                y_pos = round(i * self.note_height)
                # Octave line (below C)
                if (i % 12) == 0:
                    Color(0.4, 0.4, 0.4, 0.8)
                    width = 1.2
                # Line after E (between E and F)
                elif (i % 12) == 5:
                    Color(0.6, 0.6, 0.6, 0.6)
                    width = 1.0
                else:
                    Color(0.7, 0.7, 0.7, 0.4)
                    width = 0.6

                Line(points=[self.x, self.y + y_pos, self.x + self.width, self.y + y_pos], width=width)

            # --- Add C note labels via canvas textures ---
            Color(1, 1, 1, 1)
            for i in range(start_idx, end_idx + 1):
                if (i % 12) == 0:
                    octave_num = (i // 12) - 1
                    texture = self.get_note_texture(f"C{octave_num}")
                    y_start = round(i * self.note_height)
                    y_end = round((i + 1) * self.note_height)
                    note_h = y_end - y_start
                    # Center the label texture
                    tex_w, tex_h = texture.size
                    pos_x = self.x + (self.width - tex_w) / 2
                    pos_y = self.y + y_start + (note_h - tex_h) / 2
                    Rectangle(texture=texture, pos=(pos_x, pos_y), size=texture.size)
