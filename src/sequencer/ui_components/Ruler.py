from kivy.uix.boxlayout import BoxLayout
from kivy.uix.widget import Widget
from kivy.properties import ObjectProperty, NumericProperty
from kivy.uix.label import Label
from kivy.graphics import Color, Line
from kivy.metrics import dp

class RulerContent(Widget):
    """The actual scrolling content of the ruler with measure numbers."""
    total_beats = NumericProperty(1.0)
    beats_per_measure = NumericProperty(4.0)
    seek_callback = ObjectProperty(None)

    def redraw(self, *args):
        """Draws the measure lines and numbers."""
        self.canvas.before.clear()
        self.clear_widgets()

        if self.total_beats <= 0:
            return

        pixels_per_beat = self.width / self.total_beats
        if pixels_per_beat <= 0:
            return

        num_measures = int(self.total_beats / self.beats_per_measure)

        with self.canvas.before:
            Color(0.4, 0.4, 0.4, 1)
            for i in range(1, num_measures + 2):
                beat_pos = (i - 1) * self.beats_per_measure
                x_pos = self.x + (beat_pos * pixels_per_beat)

                # Draw vertical line for the measure
                Line(points=[x_pos, self.y, x_pos, self.y + self.height], width=1.0)

                # Add measure number label
                label = Label(
                    text=str(i),
                    font_size='10sp',
                    pos=(x_pos + dp(2), self.y),  # Add small padding
                    size=(pixels_per_beat * self.beats_per_measure, self.height),
                    halign='left',
                    valign='middle',
                    color=(0.8, 0.8, 0.8, 1)
                )
                label.text_size = label.size
                self.add_widget(label)

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos) and self.seek_callback:
            # Convert the click position within the widget to a beat number
            relative_click_x = touch.x - self.x
            if self.width == 0:
                return False

            pixels_per_beat = self.width / self.total_beats
            if pixels_per_beat == 0:
                return False

            clicked_beat = relative_click_x / pixels_per_beat
            measure = int(clicked_beat / self.beats_per_measure) + 1

            # Trigger the callback with the calculated measure
            self.seek_callback(measure)
            return True
        return super().on_touch_down(touch)

class Ruler(BoxLayout):
    """
    The main ruler widget, composed of three parts for alignment:
    - A left spacer to align with track info panels.
    - The ruler content itself.
    - A right spacer to align with track control panels.
    """
    total_beats = NumericProperty(1.0)
    beats_per_measure = NumericProperty(4.0)
    seek_callback = ObjectProperty(None)
    pixels_per_beat = NumericProperty(dp(100))

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'horizontal'

        self.left_spacer = Widget(size_hint_x=None)
        self.ruler_content = RulerContent(size_hint_x=None)
        self.right_spacer = Widget(size_hint_x=None)

        self.add_widget(self.left_spacer)
        self.add_widget(self.ruler_content)
        self.add_widget(self.right_spacer)

        # Bind properties to update the ruler content when they change
        self.bind(total_beats=self._update_ruler_content)
        self.bind(beats_per_measure=self._update_ruler_content)
        self.bind(seek_callback=self._update_ruler_content)
        self.bind(pixels_per_beat=self._update_ruler_content)

    def _update_ruler_content(self, instance, value):
        """Passes properties down to the RulerContent widget."""
        self.ruler_content.total_beats = self.total_beats
        self.ruler_content.beats_per_measure = self.beats_per_measure
        self.ruler_content.seek_callback = self.seek_callback

        # The width of the ruler content must match the track timeline's width
        self.ruler_content.width = self.total_beats * self.pixels_per_beat
        self.redraw()

    def redraw(self, *args):
        """Triggers a redraw of the ruler content."""
        self.ruler_content.redraw(*args)
