from kivymd.uix.button import MDIconButton
from kivy.uix.label import Label
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.graphics import Color, Rectangle
from kivy.clock import Clock
from kivy.properties import StringProperty

class TooltipMDIconButton(MDIconButton):
    tooltip_text = StringProperty()

    # --- Optimisation XRun ---
    # Un seul label partagé pour toutes les info-bulles afin d'éviter de créer/détruire des widgets en permanence.
    _tooltip_label = None
    _bg_rect = None
    # Suivi de l'instance qui affiche actuellement l'info-bulle.
    _active_tooltip_instance = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.tooltip_delay = 0.5  # Délai légèrement augmenté pour éviter les affichages accidentels
        self._show_event = None
        # On active les événements on_enter/on_leave
        Window.bind(mouse_pos=self.on_mouse_move)

    def on_mouse_move(self, window, pos):
        # Cette méthode est juste pour déclencher on_enter/on_leave, le contenu n'est pas nécessaire
        pass

    def on_enter(self, *args):
        """Appelé lorsque la souris entre dans la zone du widget."""
        # Si une autre info-bulle est active, on la cache
        if TooltipMDIconButton._active_tooltip_instance and TooltipMDIconButton._active_tooltip_instance != self:
            TooltipMDIconButton._active_tooltip_instance._hide_tooltip()

        TooltipMDIconButton._active_tooltip_instance = self
        if not self._show_event:
            self._show_event = Clock.schedule_once(self._show_tooltip, self.tooltip_delay)

    def on_leave(self, *args):
        """Appelé lorsque la souris quitte la zone du widget."""
        self._hide_tooltip()
        if TooltipMDIconButton._active_tooltip_instance == self:
            TooltipMDIconButton._active_tooltip_instance = None

    def _ensure_tooltip_label(self):
        """S'assure que le label global unique existe."""
        if TooltipMDIconButton._tooltip_label is None:
            label = Label(
                size_hint=(None, None),
                color=(1, 1, 1, 1),
                font_size=dp(14),
                padding=(dp(10), dp(6))
            )
            with label.canvas.before:
                Color(0, 0, 0, 0.85)
                TooltipMDIconButton._bg_rect = Rectangle(pos=label.pos, size=label.size)
            
            def update_bg(instance, value):
                TooltipMDIconButton._bg_rect.pos = instance.pos
                TooltipMDIconButton._bg_rect.size = instance.size

            label.bind(pos=update_bg, size=update_bg)
            TooltipMDIconButton._tooltip_label = label

    def _show_tooltip(self, dt):
        """Affiche et configure l'info-bulle."""
        self._ensure_tooltip_label()
        label = TooltipMDIconButton._tooltip_label

        label.text = self.tooltip_text
        label.texture_update()
        natural_size = label.texture_size
        label.size = (natural_size[0] + dp(20), dp(32))

        # Positionner l'info-bulle par rapport à la position actuelle de la souris
        x, y = Window.mouse_pos
        label.pos = (x + dp(15), y + dp(15))

        if label.parent is None:
            Window.add_widget(label)

        self._show_event = None

    def _hide_tooltip(self):
        """Cache l'info-bulle si elle est visible."""
        if self._show_event:
            self._show_event.cancel()
            self._show_event = None

        label = TooltipMDIconButton._tooltip_label
        if label and label.parent:
            Window.remove_widget(label)

    def on_release(self):
        """Force le masquage de l'info-bulle au clic."""
        super().on_release()
        self._hide_tooltip()

    def on_parent(self, instance, parent):
        """Nettoyage lorsque le widget est retiré de l'arbre graphique."""
        if parent is None:
            self._hide_tooltip()
            if TooltipMDIconButton._active_tooltip_instance == self:
                TooltipMDIconButton._active_tooltip_instance = None