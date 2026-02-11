import mido
import sounddevice as sd
import numpy as np
from pedalboard import load_plugin
import sys

# --- CONFIGURATION ---
SAMPLE_RATE = 44100
BLOCK_SIZE = 512
MIDI_PORT_INDEX = 5
DEVICE_OUT = 10

# Chemins VST3 (Pedalboard ne chargera QUE ces formats chez vous)
#VITAL_PATH = "/usr/lib/vst3/Vital.vst3/"
VITAL_PATH = "/home/gilles/.vst/CollaB3-V2-FREE/"
SURGE_PATH = "/usr/lib/vst3/Surge XT Effects.vst3/"

print("Chargement des plugins VST3...")
try:
    vital = load_plugin(VITAL_PATH)
    surge = load_plugin(SURGE_PATH)
    print("✅ Vital et Surge XT chargés avec succès.")
except Exception as e:
    print(f"❌ Erreur de chargement : {e}")
    sys.exit(1)

pending_midi = []

def audio_callback(outdata, frames, time, status):
    global pending_midi
    current_midi = pending_midi[:]
    pending_midi = []

    # Correction ici : ajout de reset=False
    audio_vital = vital.process(
        current_midi,
        duration=frames/SAMPLE_RATE,
        sample_rate=SAMPLE_RATE,
        num_channels=2,
        reset=False  # <--- CRUCIAL pour Vital
    )

    # Application de l'effet Surge
    audio_final = surge.process(
        audio_vital,
        sample_rate=SAMPLE_RATE,
        reset=False  # <--- Évite aussi les clics audio
    )

    outdata[:] = audio_final.T

# --- BOUCLE DE CONTRÔLE ---
try:
    port_name = mido.get_input_names()[MIDI_PORT_INDEX]
    with mido.open_input(port_name, callback=lambda msg: pending_midi.append(msg.copy(time=0))):
        with sd.OutputStream(device=DEVICE_OUT, channels=2, callback=audio_callback,
                             samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE):

            print(f"\n🎹 MIDI : {port_name} actif.")
            print("---------------------------------------")
            print("v : Ouvrir l'interface de VITAL")
            print("s : Ouvrir l'interface de SURGE XT")
            print("q : Quitter")
            print("---------------------------------------")

            while True:
                choix = input("Commande > ").lower()
                if choix == 'v':
                    print("Ouverture de Vital... (Fermez la fenêtre pour revenir au menu)")
                    vital.show_editor()
                elif choix == 's':
                    print("Ouverture de Surge XT... (Fermez la fenêtre pour revenir au menu)")
                    surge.show_editor()
                elif choix == 'q':
                    break
except Exception as e:
    print(f"Erreur : {e}")