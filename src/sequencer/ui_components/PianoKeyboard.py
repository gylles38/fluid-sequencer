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

        self.bind(pos=self._redraw_on_schedule, size=self._redraw_on_schedule, note_height=self._redraw_on_schedule,
                  highlighted_note=self._redraw_on_schedule)
        self._redraw_on_schedule()

    def _redraw_on_schedule(self, *args):
        # Schedule the redraw for the next frame to ensure all properties are updated.
        Clock.schedule_once(self._redraw)

    def _redraw(self, *args):
        self.canvas.clear()
        self.clear_widgets()

        highlight_color = (0.3, 0.7, 1.0, 1) # A light blue color for highlighting

        with self.canvas:
            # --- Draw White Keys Backgrounds ---
            for i in range(128):
                if (i % 12) not in [1, 3, 6, 8, 10]:
                    if i == self.highlighted_note:
                        Color(*highlight_color)
                    else:
                        Color(0.95, 0.95, 0.95, 1)
                    y_start = round(i * self.note_height)
                    y_end = round((i + 1) * self.note_height)
                    Rectangle(pos=(self.x, self.y + y_start), size=(self.width, y_end - y_start))

            # --- Draw Black Keys Backgrounds ---
            for i in range(128):
                if (i % 12) in [1, 3, 6, 8, 10]:
                    if i == self.highlighted_note:
                        Color(*highlight_color)
                    else:
                        Color(0.1, 0.1, 0.1, 1)
                    y_start = round(i * self.note_height)
                    y_end = round((i + 1) * self.note_height)
                    Rectangle(pos=(self.x, self.y + y_start), size=(self.width * 0.65, y_end - y_start))

            # --- Draw EVERY Pitch Separator (Grid sync) ---
            for i in range(1, 129):
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

        # Add C note labels
        for i in range(128):
            if (i % 12) == 0:
                octave_num = (i // 12) - 1  # MIDI note 12 is C0, 24 is C1 etc.
                y_start = round(i * self.note_height)
                y_end = round((i + 1) * self.note_height)
                note_h = y_end - y_start
                note_y = self.y + y_start
                label = Label(
                    text=f"C{octave_num}",
                    font_size=dp(9),
                    color=(0, 0, 0, 1),
                    size_hint=(None, None),
                    size=(self.width, note_h),
                    center_x=self.center_x,
                    center_y=note_y + note_h / 2,
                    halign='center',
                    valign='middle',
                )
                # Kivy's text_size is needed for alignment to work correctly
                label.text_size = label.size
                self.add_widget(label)
