import os
os.environ['KIVY_NO_ARGS'] = '1'
os.environ['KIVY_NO_CONSOLELOG'] = '1'
try:
    from sequencer.ui_components.TrackWidget import TrackWidget
    print("TrackWidget imported successfully.")
except NameError as e:
    print(f"NameError during import: {e}")
except Exception as e:
    print(f"Error during import: {e}")
