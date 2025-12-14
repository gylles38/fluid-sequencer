from kivy.uix.popup import Popup
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.metrics import dp
from sequencer.ui_components.TooltipMDIconButton import TooltipMDIconButton

class YesNoPopup(Popup):
    def __init__(self, prompt_text, callback, **kwargs):
        super(YesNoPopup, self).__init__(**kwargs)
        self.title = "Confirmation"
        self.size_hint = (0.5, 0.3)
        self.callback = callback

        layout = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
        layout.add_widget(Label(text=prompt_text))

        buttons_layout = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(10))
        yes_button = TooltipMDIconButton(icon='check', tooltip_text='Yes')
        yes_button.bind(on_press=lambda instance: self.on_answer(instance, 'y'))
        no_button = TooltipMDIconButton(icon='cancel', tooltip_text='No')
        no_button.bind(on_press=lambda instance: self.on_answer(instance, 'n'))
        buttons_layout.add_widget(yes_button)
        buttons_layout.add_widget(no_button)
        layout.add_widget(buttons_layout)
        self.content = layout

    def on_answer(self, instance, answer):
        self.callback(answer)
        self.dismiss()
