import yaml
import os
from platformdirs import user_config_dir

# Define the config directory and file path
CONFIG_DIR = user_config_dir("apollo")
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.yml")

# Global settings instance
_settings = None

def load_settings():
    """Load settings from ~/.config/apollo/settings.yml."""
    global _settings
    
    # If settings are already loaded, return them
    if _settings is not None:
        return _settings
    
    # Initialize empty settings
    _settings = {}
    
    # Try to load from file if it exists
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                file_settings = yaml.safe_load(f)
                if file_settings:
                    _settings = file_settings
        except (yaml.YAMLError, IOError) as e:
            print(f"Warning: Could not load settings from {CONFIG_FILE}: {e}")
    
    return _settings

def get_setting(key, default=None):
    """Return a setting by key or exit if missing."""
    settings = load_settings()

    # look for the key in the settings, error quit if not found
    if key not in settings:
        print(f"Error: Setting '{key}' not found in settings.")
        exit(1)
        
    return settings.get(key, default)

def save_settings(settings_dict):
    """Persist settings to ~/.config/apollo/settings.yml."""
    # Create config directory if it doesn't exist
    os.makedirs(CONFIG_DIR, exist_ok=True)
    
    # Save settings to file
    with open(CONFIG_FILE, 'w') as f:
        yaml.dump(settings_dict, f, default_flow_style=False, indent=2)

def get_apollo_folders():
    """Ensure and return Apollo working folders under PLAYLIST_SOURCE_FOLDER."""
    playlist_folder = get_setting("PLAYLIST_SOURCE_FOLDER")

    # Ensure the necessary Apollo folders exist: .apollo, .apollo/ai, .apollo/m3u, .apollo/missing, .apollo/sorted
    apollo_folder = os.path.join(playlist_folder, ".apollo")
    ai_folder = os.path.join(apollo_folder, "ai")
    m3u_folder = os.path.join(apollo_folder, "m3u")
    missing_folder = os.path.join(apollo_folder, "missing")
    sorted_folder = os.path.join(apollo_folder, "sorted")
    
    folders = [
        apollo_folder,
        ai_folder,
        m3u_folder,
        missing_folder,
        sorted_folder,
    ]
    for folder in folders:
        os.makedirs(folder, exist_ok=True)

    return playlist_folder, apollo_folder, ai_folder, m3u_folder, missing_folder, sorted_folder


# Load settings on module import
load_settings()