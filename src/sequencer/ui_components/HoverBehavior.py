from kivy.core.window import Window
from kivymd.uix.button import MDTextButton
from kivy.uix.button import Button
from kivymd.uix.slider import MDSlider

class HoverBehavior:
    """
    A Kivy mixin class that adds hover behavior to a widget. It changes the
    system cursor to a 'hand' when the mouse enters and back to an 'arrow'
    when it leaves.
    """
    def on_enter(self, *args):
        """Called when the mouse enters the widget area."""
        Window.set_system_cursor('hand')

    def on_leave(self, *args):
        """Called when the mouse leaves the widget area."""
        Window.set_system_cursor('arrow')

# Centralized Hoverable Widget Definitions
class HoverableMDTextButton(MDTextButton, HoverBehavior):
    pass

class HoverableButton(Button, HoverBehavior):
    pass

class HoverableSlider(MDSlider, HoverBehavior):
    pass
