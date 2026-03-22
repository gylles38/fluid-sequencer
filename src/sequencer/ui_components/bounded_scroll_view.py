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
        # Regression Fix: If any descendant of this ScrollView has grabbed the touch,
        # we MUST NOT scroll. This allows for rubber-band selection, note dragging,
        # automation point movement, etc. without the window moving around.
        # We check touch.grab_list because grab_current is None during the normal tree walk.
        for weak_ref in touch.grab_list:
            grabbed_widget = weak_ref()
            if grabbed_widget and grabbed_widget is not self:
                # Walk up the parent tree of the grabbed widget
                parent = grabbed_widget.parent
                while parent:
                    if parent is self:
                        return True
                    parent = parent.parent

        return super().on_touch_move(touch)
