from kivy.clock import Clock
from kivy.metrics import dp
from kivy.properties import StringProperty, BooleanProperty, ListProperty, NumericProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.modalview import ModalView
from kivymd.uix.button import MDIconButton
from kivymd.uix.label import MDLabel
from kivymd.uix.textfield import MDTextField
from kivy.core.window import Window
import re

class EditableLabel(BoxLayout):
    text = StringProperty('')
    edit_mode = BooleanProperty(False)
    font_size = NumericProperty('15sp')
    bold = BooleanProperty(False)
    color = ListProperty([1, 1, 1, 1])
    halign = StringProperty('left')
    valign = StringProperty('middle')
    adaptive_width = BooleanProperty(True)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.register_event_type('on_text_validated')
        self.label = None
        self.text_field = None
        self.overlay = None
        Clock.schedule_once(self._post_kv_init)

    def _post_kv_init(self, *args):
        self.orientation = 'horizontal'
        if self.adaptive_width:
            self.size_hint_x = None
        # Respect size_hint_y if it was set during initialization (defaults to 1 if not set)
        if self.size_hint_y is None:
            self.height = dp(36)
        self._setup_view_mode()

    def _setup_view_mode(self):
        self.clear_widgets()
        self.edit_mode = False
        
        self.label = MDLabel(
            text=self.text,
            adaptive_width=self.adaptive_width,
            font_size=self.font_size,
            bold=self.bold,
            theme_text_color="Custom",
            text_color=self.color,
            halign=self.halign,
            valign=self.valign,
            size_hint_y=1,
            shorten=not self.adaptive_width,
            shorten_from='right',
            max_lines=1
        )
        
        if self.adaptive_width:
            self.label.bind(texture_size=self._update_container_width)
        else:
            self.label.size_hint_x = 1
            self.label.bind(size=self._update_text_size)

        self.label.bind(on_touch_down=self._enter_edit_mode)
        self.add_widget(self.label)

    def _update_container_width(self, instance, size):
        if not self.edit_mode and self.adaptive_width:
            self.width = size[0]

    def _update_text_size(self, instance, size):
        if not self.adaptive_width:
            instance.text_size = size

    def _enter_edit_mode(self, instance, touch):
        if instance.collide_point(*touch.pos) and not self.edit_mode:
            self.edit_mode = True
            
            wx, wy = self.to_window(0, 0)
            
            from kivy.uix.relativelayout import RelativeLayout
            # Hauteur du conteneur ajustée à 48dp pour laisser un peu d'air
            self.overlay_container = RelativeLayout(
                size_hint=(None, None),
                size=(dp(280), dp(48)), 
                pos=(wx, wy - dp(5))
            )

            self.text_field = MDTextField(
                text=self.text,
                size_hint=(None, None),
                width=dp(200),
                # On retire 'helper_text_mode' qui causait le crash
                # On réduit la hauteur et on ajuste la police
                height=dp(36),
                pos=(dp(5), dp(6)),
                font_size=self.font_size,
                mode="filled",
                fill_color_normal=(0.15, 0.15, 0.15, 1)
            )
            # Pour masquer la ligne de soulignement et gagner de la place visuellement
            self.text_field.line_color_normal = [0, 0, 0, 0]
            self.text_field.line_color_focus = [0, 0, 0, 0]
            
            self.validate_button = MDIconButton(
                icon='check',
                # Position Y ajustée pour être centrée avec la nouvelle hauteur
                pos=(dp(210), dp(0)), 
                theme_icon_color="Custom",
                icon_color=[0, 1, 0, 1]
            )
            self.validate_button.bind(on_release=self._validate_text)
            
            self.overlay_container.add_widget(self.text_field)
            self.overlay_container.add_widget(self.validate_button)
            
            Window.add_widget(self.overlay_container)
            
            Window.bind(on_touch_down=self._check_external_click)
            Clock.schedule_once(self._request_text_field_focus, 0.2)
            Window.bind(on_key_down=self._on_key_down)

    def _check_external_click(self, instance, touch):
        # Si on clique sur le conteneur ou ses enfants, on ne fait rien (on laisse l'édition continuer)
        if self.overlay_container and self.overlay_container.collide_point(*touch.pos):
            return False 
        
        # Si on clique en dehors, on annule
        self._cleanup_edit()
        return True

    def _request_text_field_focus(self, *args):
        if self.text_field:
            self.text_field.focus = True

    def on_text_validated(self, *args):
        pass

    def on_adaptive_width(self, instance, value):
        if value:
            self.size_hint_x = None
        else:
            self.size_hint_x = 1

        if hasattr(self, 'label') and self.label:
            # We need to re-setup to update label's adaptive_width and size_hint
            self._setup_view_mode()

    def on_text(self, instance, value):
        if not self.edit_mode:
            if hasattr(self, 'label') and self.label:
                self.label.text = value

    def _validate_text(self, *args):
        """Récupère le texte, l'enregistre et ferme l'interface."""
        if hasattr(self, 'text_field') and self.text_field:
            new_text = self.text_field.text.strip()
            # Validation par Regex (lettres, chiffres et tirets uniquement)
            if new_text and re.match(r"^[a-zA-Z0-9-]*$", new_text):
                self.text = new_text
                # Envoie le signal de validation au reste de l'application
                self.dispatch('on_text_validated', self.text)
        
        # On ferme l'interface quoi qu'il arrive
        self._cleanup_edit()

    def _on_key_down(self, instance, key, scancode, codepoint, modifier):
        """Gère les touches du clavier quand le champ est actif."""
        # Touche Entrée (Code 13 ou 271 sur certains claviers numériques)
        if key in (13, 271):
            self._validate_text()
            return True
        
        # Touche Échap (Code 27)
        if key == 27:
            self._cleanup_edit()
            return True
            
        return False

    def _cleanup_edit(self, *args):
        """Nettoie les widgets et réinitialise l'affichage."""
        # 1. Supprimer le conteneur de la fenêtre principale
        if hasattr(self, 'overlay_container') and self.overlay_container:
            Window.remove_widget(self.overlay_container)
            self.overlay_container = None
            
        self.edit_mode = False
        
        # 2. Arrêter d'écouter le clavier et les clics externes
        Window.unbind(on_touch_down=self._check_external_click)
        Window.unbind(on_key_down=self._on_key_down)
        
        # 3. Revenir au mode affichage (MDLabel)
        self._setup_view_mode()