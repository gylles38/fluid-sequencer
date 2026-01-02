
from kivy.uix.screenmanager import Screen
from kivymd.app import MDApp
from kivy.lang import Builder
import os
import sys

# Ensure the src directory is in the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from sequencer.kivy_ui import SequencerLayout
from sequencer.sequencer import Sequencer

class MainScreen(Screen):
    pass

class TestApp(MDApp):
    def build(self):
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "Blue"
        # Create a sequencer instance with a dummy song for the layout to use
        sequencer = Sequencer()
        sequencer.add_track("Test MIDI Track", "midi")
        layout = SequencerLayout(sequencer=sequencer)
        return layout

    def on_start(self):
        # Schedule a screenshot after the UI is rendered
        from kivy.clock import Clock
        Clock.schedule_once(self.take_screenshot, 2)

    def take_screenshot(self, dt):
        from kivy.core.window import Window
        screenshot_path = os.path.join(os.path.dirname(__file__), 'verification_screenshot.png')
        Window.screenshot(name=screenshot_path)
        print(f"Screenshot saved to {screenshot_path}")
        self.stop()

if __name__ == '__main__':
    TestApp().run()
