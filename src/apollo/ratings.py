"""Rating reads and calculations shared by all interfaces."""

from collections import defaultdict

import pymysql

from apollo import settings
from apollo.runtime import operation


def get_db_connection():
    state = operation()
    if "database" not in state.cache:
        config = settings.current()
        connection = pymysql.connect(
            host=config.require("DATABASE_HOST"),
            user=config.require("DATABASE_UN"),
            password=config.require("DATABASE_PWD"),
            database=config.require("DATABASE_NAME"),
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=5,
            read_timeout=30,
            write_timeout=30,
        )
        state.stack.callback(connection.close)
        cursor = connection.cursor()
        state.stack.callback(cursor.close)
        state.cache["database"] = connection, cursor
    return state.cache["database"]


def rating_formula(rating, good_votes, bad_votes, skips):
    base = float(rating) if rating else 2.5
    return int(
        base * 20
        + good_votes * settings.get_setting("VOTE_STRENGTH", 5)
        - bad_votes * settings.get_setting("VOTE_STRENGTH", 5)
        - skips * settings.get_setting("SKIP_STRENGTH", 1)
    )


def records(kind, limit=50, offset=0):
    table = {"ratings": "apollo_rating", "votes": "apollo_vote", "skips": "apollo_skip"}[kind]
    _, cursor = get_db_connection()
    cursor.execute(f"SELECT COUNT(*) AS total FROM {table}")
    total = cursor.fetchone()["total"]
    cursor.execute(
        f"SELECT * FROM {table} ORDER BY artist, title LIMIT %s OFFSET %s", (limit, offset)
    )
    return {"items": cursor.fetchall(), "total": total, "limit": limit, "offset": offset}


def calculate_all_ratings(verbose=False):
    state = operation()
    if "ratings" in state.cache:
        return state.cache["ratings"]
    _, cursor = get_db_connection()
    merged = {}
    for table, fields in [
        ("apollo_rating", "artist, title, rating"),
        ("apollo_vote", "artist, title, rating"),
        ("apollo_skip", "artist, title"),
    ]:
        order = " ORDER BY modifiedon ASC" if table == "apollo_rating" else ""
        cursor.execute(f"SELECT {fields} FROM {table}{order}")
        for row in cursor.fetchall():
            key = row["artist"], row["title"]
            data = merged.setdefault(
                key,
                dict(artist=key[0], title=key[1], rating=None, good_votes=0, bad_votes=0, skips=0),
            )
            if table == "apollo_rating":
                data["rating"] = row["rating"]
            elif table == "apollo_skip":
                data["skips"] += 1
            elif row["rating"] in {"good", "bad"}:
                data[row["rating"] + "_votes"] += 1
    for data in merged.values():
        data["calculated_rating"] = rating_formula(
            data["rating"], data["good_votes"], data["bad_votes"], data["skips"]
        )
    state.cache["ratings"] = merged
    return merged


def calculate_rating(artist, title):
    return calculate_all_ratings().get(
        (artist, title),
        dict(
            artist=artist,
            title=title,
            rating=2.5,
            good_votes=0,
            bad_votes=0,
            skips=0,
            calculated_rating=50,
        ),
    )


def get_calculated_rating(artist, title):
    return calculate_rating(artist, title)["calculated_rating"]


def calculate_all_artists_ratings():
    artists = defaultdict(list)
    for song in calculate_all_ratings().values():
        artists[song["artist"]].append(song)
    return sorted(
        [_artist(name, songs) for name, songs in artists.items()],
        key=lambda row: row["average_rating"],
        reverse=True,
    )


def _artist(name, songs):
    total = sum(song["calculated_rating"] for song in songs)
    return dict(
        artist=name,
        song_count=len(songs),
        total_rating=total,
        average_rating=total / len(songs) if songs else 0,
        songs=sorted(songs, key=lambda row: row["calculated_rating"], reverse=True),
    )


def calculate_artist_rating(artist):
    return _artist(
        artist, [song for song in calculate_all_ratings().values() if song["artist"] == artist]
    )


def store_calculated_ratings(verbose=False):
    rows = list(calculate_all_ratings().values())
    connection, cursor = get_db_connection()
    try:
        cursor.execute("""CREATE TABLE IF NOT EXISTS apollo_calculated_rating (
            artist VARCHAR(255) NOT NULL, title VARCHAR(255) NOT NULL,
            calculated_rating INT NOT NULL, rating DECIMAL(4,2) NOT NULL DEFAULT 2.50,
            good_votes INT NOT NULL DEFAULT 0, bad_votes INT NOT NULL DEFAULT 0,
            skips INT NOT NULL DEFAULT 0, modifiedon TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (artist, title))""")
        cursor.executemany(
            """INSERT INTO apollo_calculated_rating
            (artist, title, calculated_rating, rating, good_votes, bad_votes, skips)
            VALUES (%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE
            calculated_rating=VALUES(calculated_rating), rating=VALUES(rating),
            good_votes=VALUES(good_votes), bad_votes=VALUES(bad_votes), skips=VALUES(skips), modifiedon=CURRENT_TIMESTAMP""",
            [
                (
                    r["artist"],
                    r["title"],
                    r["calculated_rating"],
                    r["rating"] or 2.5,
                    r["good_votes"],
                    r["bad_votes"],
                    r["skips"],
                )
                for r in rows
                if r["artist"] and r["title"]
            ],
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {"stored": len(rows)}
