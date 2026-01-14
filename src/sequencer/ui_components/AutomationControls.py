from . import *
from kivy.app import App
from kivy.clock import Clock
from kivy.properties import StringProperty
from kivy.event import EventDispatcher

class AutomationControls(BoxLayout, EventDispatcher):
    selected_param = StringProperty(None, allownone=True)
    track_type = StringProperty('midi')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.register_event_type('on_selection_change')
        self.orientation = 'horizontal'
        self.spacing = dp(4)
        self.size_hint_x = None
        self.width = 0
        self.buttons = {}
        self.bind(track_type=self.build_buttons)
        self.build_buttons()

    def build_buttons(self, *args):
        self.clear_widgets()
        self.buttons.clear()
        self.width = 0

        param_map = {
            'Volume': 'vol', 'Pan': 'pan', 'Velocity': 'vel', 'Program Change': 'prog'
        }

        if self.track_type == 'midi':
            automation_types = [
                ("volume-high", "Volume", "vol"),
                ("swap-horizontal", "Pan", "pan"),
                ("speedometer", "Velocity", "vel"),
                ("music-box-outline", "Program Change", "prog"),
            ]
        else: # audio
            automation_types = [
                ("volume-high", "Volume", "vol"),
                ("swap-horizontal", "Pan", "pan")
            ]

        for icon, tooltip, param_name in automation_types:
            button = TooltipMDIconButton(
                icon=icon,
                tooltip_text=tooltip,
                theme_icon_color="Custom",
                icon_color=[1, 1, 1, 0.8],
                md_bg_color=[0.2, 0.2, 0.2, 1],
                size_hint=(None, None),
                size=(dp(36), dp(36))
            )
            button.param_name = param_name
            button.bind(on_press=self._on_button_press)
            self.add_widget(button)
            self.buttons[param_name] = button
            self.width += dp(36) + self.spacing
            
        # Sélection automatique du premier bouton (Volume) au démarrage
        if automation_types:
            first_param = automation_types[0][2] # Récupère 'vol'
            # On utilise Clock.schedule_once pour s'assurer que l'App est bien prête
            # et que les couleurs peuvent être appliquées
            Clock.schedule_once(lambda dt: self.select_param(first_param), 0)     

    def select_param(self, param_name):
        if param_name == self.selected_param:
            return # Do nothing if the same button is clicked

        self.selected_param = param_name
        self._update_button_states()
        self.dispatch('on_selection_change', self.selected_param)

    def _on_button_press(self, instance):
        self.select_param(instance.param_name)

    def _update_button_states(self, *args):
        for param, button in self.buttons.items():
            if param == self.selected_param:
                button.md_bg_color = App.get_running_app().theme_cls.primaryColor
                button.icon_color = [1, 0.6, 0, 1] # orange vif
            else:
                button.icon_color = [1, 1, 1, 0.8]
                button.md_bg_color = [0.2, 0.2, 0.2, 1]

    def on_selection_change(self, *args):
        pass # Kivy event dispatcher requires this method to exist
