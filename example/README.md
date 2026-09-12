Copy settings.yml to /mnt/fast/apollo/settings.yml and set the paths and integration credentials you use. The configured music and playlist directories must already exist and be accessible from the host running Apollo.

priority.yml is optional. Place it beside your selected settings file to adjust tie-breaking between equally matched files. Without it, Apollo uses format and bitrate selection alone.

Run `apollo doctor` for read-only configuration and connectivity checks, then `apollo serve` for the web interface. See the repository README for all configuration fields and commands.
