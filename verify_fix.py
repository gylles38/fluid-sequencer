import os
import subprocess
import time

def run_and_screenshot():
    """
    Runs the Kivy application and takes a screenshot.
    Assumes this script is run inside xvfb-run.
    """
    command = [
        'python3',
        '-m',
        'sequencer.main',
        '--',
        '--gui',
        'projects/two_midi_tracks.json'
    ]

    env = os.environ.copy()
    if 'PYTHONPATH' in env:
        env['PYTHONPATH'] = f"src:{env['PYTHONPATH']}"
    else:
        env['PYTHONPATH'] = 'src'

    # Start the application in the background
    app_process = subprocess.Popen(command, env=env)

    # Wait for the application to initialize
    print("Waiting for application to start...")
    time.sleep(7)

    # Take the screenshot
    print("Taking screenshot...")
    result = subprocess.run(['scrot', 'verification.png'])
    if result.returncode == 0:
        print("Screenshot saved as verification.png")
    else:
        print(f"Scrot failed with return code: {result.returncode}")
        print(f"Scrot stdout: {result.stdout}")
        print(f"Scrot stderr: {result.stderr}")

    # Terminate the application
    print("Terminating application...")
    app_process.terminate()
    app_process.wait()
    print("Application terminated.")

if __name__ == '__main__':
    run_and_screenshot()
