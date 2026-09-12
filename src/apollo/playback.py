"""Publish a playlist and request playback over MQTT."""

from paho.mqtt import publish

from apollo import playlist, settings

PLAYLIST_TOPIC = "nodered/playlist"


def play(name=None):
    """Publish the latest M3U before asking Node-RED to play it."""
    config = settings.current()
    name = (name or config.require("DYNAMIC_PLAYLIST_FILE")).removesuffix(".txt")
    published = playlist.write_m3u_files(name)
    publish.single(
        PLAYLIST_TOPIC,
        payload=name,
        hostname=config.require("MQTT_SERVER"),
        port=config.require("MQTT_PORT"),
        auth={
            "username": config.require("MQTT_UN"),
            "password": config.require("MQTT_PWD"),
        },
    )
    return {
        "played": True,
        "playlist": name,
        "published": published,
        "topic": PLAYLIST_TOPIC,
        "payload": name,
    }
