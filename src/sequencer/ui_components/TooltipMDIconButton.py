from kivymd.uix.button import MDIconButton
from kivy.uix.label import Label
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.graphics import Color, Rectangle
from kivy.clock import Clock
from kivy.properties import StringProperty

class TooltipMDIconButton(MDIconButton):
    tooltip_text = StringProperty()

    # NOUVEAU : État de classe pour suivre le dernier bouton survolé
    # Ceci est critique pour la logique des boutons adjacents.
    _active_instance = None

    def __init__(self, **kwargs):
        self.tooltip_text = kwargs.pop("tooltip_text", "")
        super().__init__(**kwargs)
        self.tooltip_delay = 0.2
        self.tooltip_label = None
        self._show_event = None
        # La liaison Window.bind est gérée dans on_parent
        self.bind(tooltip_text=self._on_tooltip_text_changed)

    def _on_tooltip_text_changed(self, instance, value):
        if self.tooltip_label:
            self.tooltip_label.text = value
            self.tooltip_label.texture_update()
            natural_size = self.tooltip_label.texture_size
            new_size = (natural_size[0] + dp(20), dp(32)) if natural_size else (dp(120), dp(32))
            self.tooltip_label.size = new_size
            if hasattr(self, "_bg_rect"):
                self._bg_rect.size = new_size

    def on_mouse_pos(self, window, pos):
        collide = self.collide_point(*self.to_widget(*pos))
        
        if collide:
            # === LOGIQUE DE PRISE DE CONTRÔLE (RÈGLEMENT DU CONFLIT ENTRE BOUTONS) ===
            # Si un autre bouton est actif, on le force à se masquer avant de continuer
            if TooltipMDIconButton._active_instance and TooltipMDIconButton._active_instance is not self:
                TooltipMDIconButton._active_instance._hide_tooltip()
                
            # Définir cette instance comme l'instance active
            TooltipMDIconButton._active_instance = self
            # =========================================================================

            self._ensure_tooltip()
            self._update_tooltip_position(pos)
            if not self._show_event:
                self._show_event = Clock.schedule_once(self._do_show_tooltip, self.tooltip_delay)
        else:
            # Vérification essentielle : si le curseur n'est plus sur le bouton, mais qu'il est sur le tooltip
            is_over_tooltip = False
            if self.tooltip_label and self.tooltip_label.parent and self.tooltip_label.collide_point(*pos):
                is_over_tooltip = True
            
            if not is_over_tooltip:
                self._hide_tooltip()

    def _ensure_tooltip(self):
        if self.tooltip_label:
            return
        self.tooltip_label = Label(
            text=self.tooltip_text,
            size_hint=(None, None),
            color=(1, 1, 1, 1),
            font_size=dp(14),
            padding=(dp(10), dp(6))
        )
        self.tooltip_label.texture_update()
        natural_size = self.tooltip_label.texture_size
        self.tooltip_label.size = (natural_size[0] + dp(20), dp(32)) if natural_size else (dp(120), dp(32))
        with self.tooltip_label.canvas.before:
            Color(0, 0, 0, 0.85)
            self._bg_rect = Rectangle(pos=self.tooltip_label.pos, size=self.tooltip_label.size)
        self.tooltip_label.bind(pos=self._update_bg, size=self._update_bg)

    def _update_bg(self, instance, value):
        if hasattr(self, "_bg_rect"):
            self._bg_rect.pos = instance.pos
            self._bg_rect.size = instance.size

    def _do_show_tooltip(self, dt):
        if self.tooltip_label and self.tooltip_label.parent is None:
            Window.add_widget(self.tooltip_label)
        self._show_event = None

    def _update_tooltip_position(self, mouse_pos):
        if not self.tooltip_label:
            return
        x, y = mouse_pos
        self.tooltip_label.pos = (x + dp(10), y + dp(10))
        self._update_bg(self.tooltip_label, None)

    def _hide_tooltip(self):
        if self._show_event:
            self._show_event.cancel()
            self._show_event = None
            
        if self.tooltip_label and self.tooltip_label.parent:
            Window.remove_widget(self.tooltip_label)
            
        # NOUVEAU : Réinitialiser l'état global
        if TooltipMDIconButton._active_instance is self:
            TooltipMDIconButton._active_instance = None
            
    def on_release(self):
        """Force le masquage du tooltip immédiatement après un clic (relâchement)."""
        super().on_release()
        self._hide_tooltip()  

    def on_parent(self, instance, parent):
        if parent is None:
            self._hide_tooltip()
            # DÉLIER le suivi de la souris 
            Window.unbind(mouse_pos=self.on_mouse_pos) 
            # NOUVEAU : Réinitialiser l'état global si on retire le bouton
            if TooltipMDIconButton._active_instance is self:
                TooltipMDIconButton._active_instance = None
            if self.tooltip_label:
                self.tooltip_label = None
        else:
            # RELIER le suivi de la souris (quand le bouton est ajouté)
            Window.bind(mouse_pos=self.on_mouse_pos)