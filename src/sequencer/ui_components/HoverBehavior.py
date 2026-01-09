from kivy.core.window import Window
from kivymd.uix.button import MDButton
from kivy.uix.button import Button

class HoverBehavior:
    """
    A Kivy mixin class that adds hover behavior to a widget. It changes the
    system cursor to a 'hand' when the mouse enters and back to an 'arrow'
    when it leaves.
    """
    def __init__(self, *args, **kwargs):
        self.register_event_type('on_enter')
        self.register_event_type('on_leave')
        super().__init__(*args, **kwargs)

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
