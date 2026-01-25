
import os
import sys
import time

# Add src to path
sys.path.append(os.path.join(os.getcwd(), 'src'))

# Mocking some parts if needed, but we want to test the real UI
os.environ['KIVY_NO_ARGS'] = '1'
os.environ['KIVY_USE_DEFAULTCONFIG'] = '1'

from kivy.config import Config
Config.set('graphics', 'width', '1024')
Config.set('graphics', 'height', '768')

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from sequencer.kivy_ui import SequencerApp

def take_screenshot(app):
    print("Taking screenshot...")
    Window.screenshot('/home/jules/verification/gui_screenshot.png')
    print("Screenshot saved to /home/jules/verification/gui_screenshot.png")
    app.stop()

class VerifiedApp(SequencerApp):
    def on_start(self):
        # Wait a bit for layout to finish
        Clock.schedule_once(lambda dt: take_screenshot(self), 2.0)

if __name__ == "__main__":
    if not os.path.exists('/home/jules/verification'):
        os.makedirs('/home/jules/verification')

    VerifiedApp().run()
