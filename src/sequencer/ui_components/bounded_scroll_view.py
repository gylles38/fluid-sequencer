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
        # This is a specific fix for the PianoRollEditor.
        # If the child is an EditablePianoRollViewer and a note is being dragged,
        # consume the event to prevent this ScrollView from scrolling.
        if self.children:
            child = self.children[0]
            # Check for the specific class name to avoid circular imports
            if child.__class__.__name__ == 'EditablePianoRollViewer':
                if child.grid._dragged_note and touch.grab_current is child.grid:
                    return True
        return super().on_touch_move(touch)
