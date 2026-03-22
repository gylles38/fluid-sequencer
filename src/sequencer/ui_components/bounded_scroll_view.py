from kivy.uix.scrollview import ScrollView
from kivy.core.window import Window


class BoundedScrollView(ScrollView):
    """
    A ScrollView that only handles touch events within its own bounds.
    This prevents it from "stealing" events from sibling widgets.
    """
    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            Window.set_system_cursor('hand')
            return super().on_touch_down(touch)
        return False

    def on_touch_up(self, touch):
        Window.set_system_cursor('arrow')
        return super().on_touch_up(touch)

    def on_touch_move(self, touch):
        # Regression Fix: If the direct child of this ScrollView has grabbed the touch,
        # we MUST NOT scroll. This allows for rubber-band selection, note dragging, etc.
        # without the window moving around.
        if self.children and touch.grab_current is self.children[0]:
            return True

        return super().on_touch_move(touch)
