from . import *
from kivy.app import App

class AutomationControls(BoxLayout):
    def __init__(self, track_type='midi', **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'horizontal'
        self.spacing = dp(4)
        self.size_hint_x = None
        self.width = 0 # Will be set dynamically

        self.buttons = []
        self.selected_button = None

        if track_type == 'midi':
            automation_types = [
                ("volume-high", "Volume"),
                ("swap-horizontal", "Pan"),
                ("speedometer", "Velocity"),
                ("music-box-outline", "Program Change"),
                ("knob", "Control Change")
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
            button.bind(on_press=self._on_button_press)
            self.add_widget(button)
            self.buttons.append(button)
            self.width += dp(36) + self.spacing

    def _on_button_press(self, instance):
        if self.selected_button == instance:
            # If the logic was to allow deselecting, it would go here.
            # Based on requirements, we do nothing if the same button is pressed.
            return

        # Deselect the old button
        if self.selected_button:
            self.selected_button.md_bg_color = [0.2, 0.2, 0.2, 1]
            self.selected_button.icon_color = [1, 1, 1, 0.8]

        # Select the new button
        instance.md_bg_color = App.get_running_app().theme_cls.primaryColor
        instance.icon_color = [1, 0.6, 0, 1]
        self.selected_button = instance
