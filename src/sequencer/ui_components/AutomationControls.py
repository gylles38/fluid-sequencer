from . import *
from kivy.app import App

class AutomationControls(BoxLayout):
    def __init__(self, track_type='midi', on_selection_change=None, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'horizontal'
        self.spacing = dp(4)
        self.size_hint_x = None
        self.width = 0
        self.on_selection_change = on_selection_change

        self.buttons = []
        self.selected_button = None

        param_map = {
            'Volume': 'vol',
            'Pan': 'pan',
            'Velocity': 'vel',
            'Program Change': 'prog',
            'Control Change': 'cc' # This is a placeholder, might need more specific handling
        }

        if track_type == 'midi':
            automation_types = [
                ("volume-high", "Volume"),
                ("swap-horizontal", "Pan"),
                ("speedometer", "Velocity"),
                ("music-box-outline", "Program Change"),
                # ("knob", "Control Change") # CC is more complex, handle later
            ]
        else: # audio
            automation_types = [
                ("volume-high", "Volume"),
                ("swap-horizontal", "Pan")
            ]

        for icon, tooltip in automation_types:
            button = TooltipMDIconButton(
                icon=icon,
                tooltip_text=tooltip,
                theme_icon_color="Custom",
                icon_color=[1, 1, 1, 0.8],
                md_bg_color=[0.2, 0.2, 0.2, 1],
                size_hint=(None, None),
                size=(dp(36), dp(36))
            )
            button.param_name = param_map.get(tooltip)
            button.bind(on_press=self._on_button_press)
            self.add_widget(button)
            self.buttons.append(button)
            self.width += dp(36) + self.spacing
            
        # Sélection automatique du premier bouton (Volume) au démarrage
        if self.buttons:
            first_button = self.buttons[0]
            # On utilise Clock pour être sûr que l'interface est prête
            Clock.schedule_once(lambda dt: self._on_button_press(first_button))

    def _on_button_press(self, instance):
        selected_param = None

        if self.selected_button == instance:
            # Désélection (votre code actuel)
            self.selected_button.md_bg_color = [0.2, 0.2, 0.2, 1]
            self.selected_button.icon_color = [1, 1, 1, 0.8]
            self.selected_button = None
            selected_param = None
        else:
            # Désélection de l'ancien
            if self.selected_button:
                self.selected_button.md_bg_color = [0.2, 0.2, 0.2, 1]
                self.selected_button.icon_color = [1, 1, 1, 0.8]

            # Sélection du nouveau (Orange vif)
            self.selected_button = instance
            instance.icon_color = [1, 0.6, 0, 1] # Votre orange vif
            selected_param = instance.param_name

        if self.on_selection_change:
            self.on_selection_change(selected_param)            
