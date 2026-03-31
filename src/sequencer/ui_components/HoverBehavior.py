from kivy.core.window import Window
from kivymd.uix.button import MDButton
from kivy.uix.button import Button
from .ui_utils import set_safe_cursor, GlobalHoverManager
import time

class HoverBehavior:
    """
    A Kivy mixin class that adds hover behavior to a widget. It changes the
    system cursor to a 'hand' when the mouse enters and back to an 'arrow'
    when it leaves.
    """
    def __init__(self, *args, **kwargs):
        self.register_event_type('on_enter')
        self.register_event_type('on_leave')
        self.hovered = False
        super().__init__(*args, **kwargs)
        self.bind(on_parent=self._on_hover_parent)

    def _on_hover_parent(self, instance, parent):
        if parent is not None:
            GlobalHoverManager().register(self)
        else:
            if self.hovered:
                self.hovered = False
                self.dispatch('on_leave')

    def _on_mouse_pos_internal(self, pos):
        if not self.get_root_window():
            return

        # 1. Quick collision check first (Window coordinates)
        # Performance: avoid expensive tree walks if mouse is nowhere near
        wx, wy = self.to_window(0, 0)
        inside = (wx <= pos[0] <= wx + self.width) and \
                 (wy <= pos[1] <= wy + self.height)

        if not inside:
            if self.hovered:
                self.hovered = False
                self.dispatch('on_leave')
            return

        # 2. Deep visibility check: Ensure all ancestors are enabled and visible
        # Only performed if the mouse is actually inside the bounding box.
        if getattr(self, 'disabled', False) or getattr(self, 'opacity', 1) < 0.01 or self.width <= 0 or self.height <= 0:
            if self.hovered:
                self.hovered = False
                self.dispatch('on_leave')
            return

        # Occultation check: traverse Window children to see if something is on top of us
        # But only if we are in the Window (which we checked above)
        root = self.get_root_window()
        # Find which child of root contains the touch and where we are in the stack

        # Performance: simple occlusion check by looking at the widget stack
        # A more robust check would involve walking the tree, but for performance
        # we focus on our direct parent visibility first.
        p = self.parent
        while p:
            if getattr(p, 'disabled', False) or getattr(p, 'opacity', 1) < 0.01:
                if self.hovered:
                    self.hovered = False
                    self.dispatch('on_leave')
                return
            p = p.parent

        # Final check: is there a modal or another widget covering us?
        # We walk backwards through Window children (from top to bottom)
        for child in reversed(root.children):
            if child is self:
                break
            # If the child is a ModalView or a FloatingWindow or something that spans the screen
            if child.collide_point(*pos) and getattr(child, 'opacity', 1) > 0.5:
                # Something is on top of us
                if self.hovered:
                    self.hovered = False
                    self.dispatch('on_leave')
                return

        if not self.hovered:
            self.hovered = True
            self.dispatch('on_enter')

    def on_enter(self, *args):
        """Called when the mouse enters the widget area."""
        # from kivy.logger import Logger
        # Logger.info(f"HoverBehavior: on_enter for {self}")
        set_safe_cursor('hand')

    def on_leave(self, *args):
        """Called when the mouse leaves the widget area."""
        # from kivy.logger import Logger
        # Logger.info(f"HoverBehavior: on_leave for {self}")
        set_safe_cursor('arrow')

class HoverableMDButton(MDButton, HoverBehavior):
    pass

class HoverableButton(Button, HoverBehavior):
    pass
