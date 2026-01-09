from kivy.core.window import Window
from kivy.properties import BooleanProperty
from kivymd.uix.button import MDButton
from kivy.uix.button import Button

class HoverBehavior:
    """
    A Kivy mixin class that adds hover behavior to a widget. It changes the
    system cursor to a 'hand' when the mouse enters and back to an 'arrow'
    when it leaves.
    """
    hovered = BooleanProperty(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.register_event_type('on_enter')
        self.register_event_type('on_leave')
        Window.bind(mouse_pos=self.on_mouse_pos)

    def on_mouse_pos(self, window, pos):
        if not self.get_root_window():
            return

        # Check if the mouse is over this widget
        inside = self.collide_point(*self.to_widget(*pos))
        if self.hovered == inside:
            return

        self.hovered = inside
        if inside:
            self.dispatch('on_enter')
        else:
            self.dispatch('on_leave')

    def on_enter(self, *args):
        """Called when the mouse enters the widget area."""
        Window.set_system_cursor('hand')

    def on_leave(self, *args):
        """Called when the mouse leaves the widget area."""
        Window.set_system_cursor('arrow')

class HoverableMDButton(HoverBehavior, MDButton):
    pass

class HoverableButton(HoverBehavior, Button):
    pass
