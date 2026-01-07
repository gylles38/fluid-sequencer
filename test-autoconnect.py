import subprocess
import time

def find_port_by_name(pattern):
    """
    Cherche un port JACK complet qui contient le 'pattern' donné.
    Retourne le nom complet du premier port trouvé, ou None.
    """
    try:
        # On demande à JACK/PipeWire la liste de tous les ports
        result = subprocess.run(["jack_lsp"], capture_output=True, text=True)
        all_ports = result.stdout.splitlines()
        
        for port in all_ports:
            # On cherche une correspondance partielle (ex: "RtMidiOut" dans le nom complet)
            if pattern in port:
                return port.strip() # On nettoie les espaces/sauts de ligne
                
        return None
    except FileNotFoundError:
        print("Erreur: commande 'jack_lsp' introuvable.")
        return None

def auto_connect_dynamic(src_keyword, dest_keyword):
    """
    Connecte deux ports en utilisant des mots-clés partiels.
    """
    print(f"--- Recherche des ports pour : '{src_keyword}' -> '{dest_keyword}' ---")
    
    # 1. On attend un peu que les ports soient créés (important au démarrage du script)
    time.sleep(0.5)

    # 2. Recherche des noms complets
    full_source = find_port_by_name(src_keyword)
    full_dest = find_port_by_name(dest_keyword)

    if not full_source:
        print(f"❌ Source introuvable avec le mot-clé : '{src_keyword}'")
        return
    if not full_dest:
        print(f"❌ Destination introuvable avec le mot-clé : '{dest_keyword}'")
        return

    print(f"✅ Ports identifiés :\n   Source : {full_source}\n   Dest   : {full_dest}")

    # 3. Tentative de connexion via jack_connect
    try:
        res = subprocess.run(
            ["jack_connect", full_source, full_dest],
            capture_output=True, 
            text=True
        )
        
        if res.returncode == 0:
            print("🚀 Connexion réussie !")
        else:
            # Si erreur (souvent car déjà connecté), on affiche le message
            # PipeWire renvoie souvent une erreur si c'est déjà connecté, ce n'est pas grave.
            if "exists" in res.stderr:
                 print("ℹ️  Déjà connectés.")
            else:
                 print(f"⚠️ Erreur de connexion : {res.stderr.strip()}")

    except FileNotFoundError:
        print("Erreur: commande 'jack_connect' introuvable.")

# ==========================================
# EXEMPLE D'UTILISATION DANS VOTRE CODE
# ==========================================

# Mots-clés suffisamment uniques pour identifier vos ports
# Plus besoin de mettre "(capture_0)", juste la partie fixe du nom.

# Pour la source (votre script python) :
ma_source = "rhodes" 

# Pour la destination (Carla) :
# Tapez 'jack_lsp' pour trouver un mot clé unique à votre plugin ou rack
# Souvent "Carla:events-in" fonctionne pour l'entrée principale
ma_destination = "Rhodes2_by_giloux:events-in" 

auto_connect_dynamic(ma_source, ma_destination)
