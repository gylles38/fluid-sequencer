import os
import time
from kivy.clock import Clock
from kivy.core.window import Window
from sequencer.kivy_ui import SequencerApp

def run_verification():
    app = SequencerApp()

    def on_app_started(dt):
        # 1. Add some tracks
        app.sequencer_layout.process_command_ui('add "Track 1"')
        app.sequencer_layout.process_command_ui('add "Track 2"')

        # 2. Open Input Routing Editor
        app.sequencer_layout.open_input_routing_editor()

        # 3. Wait for UI to settle and take screenshot
        def take_screenshot(dt):
            Window.screenshot("/home/jules/verification/bridge_ui.png")
            app.stop()

        Clock.schedule_once(take_screenshot, 2.0)

    Clock.schedule_once(on_app_started, 1.0)
    app.run()

if __name__ == "__main__":
    if not os.path.exists("/home/jules/verification"):
        os.makedirs("/home/jules/verification")
    run_verification()
