from kivy.core.window import Window
from kivymd.uix.button import MDButton
from kivy.uix.button import Button

from kivy.properties import BooleanProperty

class HoverBehavior:
    """
    A Kivy mixin class that adds hover behavior to a widget. It changes the
    system cursor to a 'hand' when the mouse enters and back to an 'arrow'
    when it leaves.
    """
    hovered = BooleanProperty(False)

    def __init__(self, *args, **kwargs):
        self.register_event_type('on_enter')
        self.register_event_type('on_leave')
        super().__init__(*args, **kwargs)
        Window.bind(mouse_pos=self._check_hover)

    def _check_hover(self, instance, pos):
        if not self.get_root_window():
            return

        # Check if the mouse position is within the widget's boundaries
        # Use to_local to get coordinates relative to the widget's (0,0)
        local_x, local_y = self.to_local(*pos)
        if 0 <= local_x <= self.width and 0 <= local_y <= self.height:
            if not self.hovered:
                self.hovered = True
                self.dispatch('on_enter')
        else:
            if self.hovered:
                self.hovered = False
                self.dispatch('on_leave')

    def on_enter(self, *args):
        """Called when the mouse enters the widget area."""
        Window.set_system_cursor('hand')

    def on_leave(self, *args):
        """Called when the mouse leaves the widget area."""
        Window.set_system_cursor('arrow')

class HoverableMDButton(MDButton, HoverBehavior):
    pass

class HoverableButton(Button, HoverBehavior):
    pass
