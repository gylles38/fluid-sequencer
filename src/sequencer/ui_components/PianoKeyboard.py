from kivy.uix.floatlayout import FloatLayout
from kivy.properties import NumericProperty, ListProperty
from kivy.graphics import Color, Rectangle, Line
from kivy.uix.label import Label
from kivy.metrics import dp
from kivy.clock import Clock

class PianoKeyboard(FloatLayout):
    """
    A widget that draws a vertical piano keyboard.
    """
    note_height = NumericProperty(dp(14))
    highlighted_notes = ListProperty([]) # List of MIDI note numbers to highlight

    def __init__(self, **kwargs):
        super(PianoKeyboard, self).__init__(**kwargs)
        self.size_hint = (None, None)
        self.height = 128 * self.note_height
        self.width = dp(40)

        self.bind(pos=self._redraw_on_schedule, size=self._redraw_on_schedule,
                  highlighted_notes=self._redraw_on_schedule)
        self._redraw_on_schedule()

    def _redraw_on_schedule(self, *args):
        # Schedule the redraw for the next frame to ensure all properties are updated.
        Clock.schedule_once(self._redraw)

    def _redraw(self, *args):
        self.canvas.before.clear()
        self.clear_widgets()

        highlight_color = (0.3, 0.7, 1.0, 1) # A light blue color for highlighting

        with self.canvas.before:
            # Draw white keys
            for i in range(128):
                if (i % 12) not in [1, 3, 6, 8, 10]:
                    if i in self.highlighted_notes:
                        Color(*highlight_color)
                    else:
                        Color(0.95, 0.95, 0.95, 1)
                    note_y = self.y + i * self.note_height
                    Rectangle(pos=(self.x, note_y), size=(self.width, self.note_height))

            # Draw lines between white keys
            Color(0.7, 0.7, 0.7, 1)
            for i in range(128):
                if (i % 12) not in [1, 3, 6, 8, 10]:
                    note_y = self.y + i * self.note_height
                    width = 1.1 if (i % 12) in [4, 11] else 0.6 # Thicker line after E and B
                    Line(points=[self.x, note_y, self.x + self.width, note_y], width=width)

            # Draw black keys
            for i in range(128):
                 if (i % 12) in [1, 3, 6, 8, 10]:
                    if i in self.highlighted_notes:
                        Color(*highlight_color)
                    else:
                        Color(0.1, 0.1, 0.1, 1)
                    note_y = self.y + i * self.note_height
                    Rectangle(pos=(self.x, note_y), size=(self.width * 0.65, self.note_height))

        # Add C note labels
        for i in range(128):
            if (i % 12) == 0:
                octave_num = (i // 12) - 1  # MIDI note 12 is C0, 24 is C1 etc.
                note_y = self.y + i * self.note_height
                label = Label(
                    text=f"C{octave_num}",
                    font_size=dp(9),
                    color=(0, 0, 0, 1),
                    size_hint=(None, None),
                    size=(self.width, self.note_height),
                    center_x=self.center_x,
                    center_y=note_y + self.note_height / 2,
                    halign='center',
                    valign='middle',
                )
                # Kivy's text_size is needed for alignment to work correctly
                label.text_size = label.size
                self.add_widget(label)
