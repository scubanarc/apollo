import pymysql
import os
from colorama import Fore, Style
import json
import sys
from apollo_lib import settings

def get_db_connection():
    """Establish and return a pymysql connection and cursor with utf8mb4 encoding."""
    db_host = settings.get_setting("DATABASE_HOST")
    db_user = settings.get_setting("DATABASE_UN")
    db_pass = settings.get_setting("DATABASE_PWD")
    db_name = settings.get_setting("DATABASE_NAME")

    try:
        dbh = pymysql.connect(
            host=db_host,
            user=db_user,
            password=db_pass,
            database=db_name,
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )
        sth = dbh.cursor()
        # Set UTF8MB4 for the session
        sth.execute("SET NAMES utf8mb4")
        dbh.commit()
        return dbh, sth
    except pymysql.MySQLError as e:
        print(f"Failed to connect to '{db_host}:{db_name}' using username: {db_user}")
        print(e)
        return None, None


def rating_formula(rating, good_votes, bad_votes, skips):
    """
    Defines the formula for calculating the final rating.
    As stored in the database, rating is from 1 to 5
    # therefore, 2.5 is neutral or unrated
    # a song with no rating, no skips, and no votes should get a score of 50
    # it is possible to go below 0 and above 100
    """
    base = rating if rating else 2.5
    vote_strength = settings.get_setting("VOTE_STRENGTH", 5)
    skip_strength = settings.get_setting("SKIP_STRENGTH", 1)

    calculated_rating = int((base * 20) + (good_votes * vote_strength) - (bad_votes * vote_strength) - (skips * skip_strength))
    return calculated_rating

def calculate_rating(artist, title):
    """
    Calculate the rating for a given artist and title.
    This function connects to the database, retrieves the ratings, votes, and skips,
    and calculates the final rating based on the formula.
    It is used to calculate the final rating during playback, not during playlist creation.
    """
    dbh, sth = get_db_connection()

    # votes
    good_votes = 0
    bad_votes = 0

    sth.execute("SELECT * FROM apollo_vote WHERE artist=%s AND title=%s", (artist, title))
    ratings = sth.fetchall()

    if ratings:
        for row in ratings:
            vote = row['rating']
            if(vote == "good"):
                good_votes += 1
            elif(vote == "bad"):
                bad_votes += 1

    # skips
    skips = 0
    sth.execute("SELECT COUNT(*) AS skip_count FROM apollo_skip WHERE artist=%s AND title=%s", (artist, title))
    rows = sth.fetchone()
    skips = rows['skip_count'] if rows and 'skip_count' in rows else 0

    # ratings
    rating = 0
    sth.execute("SELECT * FROM apollo_rating WHERE artist=%s AND title=%s ORDER BY modifiedon ASC", (artist, title))
    ratings = sth.fetchall()

    if ratings:
        for row in ratings:
            rating = int(row['rating'])

    if(rating == 0):
        rating = 2.5

    calculated_rating = rating_formula(rating, good_votes, bad_votes, skips)

    json_data = {
        "artist": artist,
        "title": title,
        "calculated_rating": calculated_rating,
        "good_votes": good_votes,
        "bad_votes": bad_votes,
        "skips": skips,
        "rating": rating
    }

    json_string = json.dumps(json_data)

    print(json_string)


calculated_ratings = None

def calculate_all_ratings(verbose=False):
    """
    Calculate ratings for all songs in the database into an in-memory dictionary.
    This function connects to the database, retrieves all ratings, votes, and skips,
    and calculates the final ratings for each song based on the formula.
    It is used to calculate ratings for all songs at playlist creation time.
    """
    dbh, sth = get_db_connection()

    sth.execute("SELECT artist, title, rating FROM apollo_rating")
    ratings_rows = sth.fetchall()

    sth.execute("SELECT artist, title, rating FROM apollo_vote")
    vote_rows = sth.fetchall()

    sth.execute("SELECT artist, title FROM apollo_skip")
    skip_rows = sth.fetchall()

    merged = {}

    for row in ratings_rows:
        key = (row.get('artist'), row.get('title'))
        if key not in merged:
            merged[key] = {"artist": key[0], "title": key[1], "rating": None, "good_votes": 0, "bad_votes": 0, "skips": 0}
        merged[key]["rating"] = row.get('rating')

    for row in vote_rows:
        key = (row.get('artist'), row.get('title'))
        if key not in merged:
            merged[key] = {"artist": key[0], "title": key[1], "rating": None, "good_votes": 0, "bad_votes": 0, "skips": 0}
        v = row.get('rating')
        if v == 'good':
            merged[key]["good_votes"] += 1
        elif v == 'bad':
            merged[key]["bad_votes"] += 1

    for row in skip_rows:
        key = (row.get('artist'), row.get('title'))
        if key not in merged:
            merged[key] = {"artist": key[0], "title": key[1], "rating": None, "good_votes": 0, "bad_votes": 0, "skips": 0}
        merged[key]["skips"] += 1

    for key, data in merged.items():
        try:
            r = data.get('rating')
            try:
                r_val = float(r) if r is not None else 0
            except (TypeError, ValueError):
                r_val = 0
            calculated_rating = rating_formula(r_val, data.get('good_votes', 0), data.get('bad_votes', 0), data.get('skips', 0))
            # print(f"Calculated rating for {key}: {calculated_rating}")
            data['calculated_rating'] = calculated_rating
        except Exception:
            pass

    if verbose:
        # sort based on calculated_rating
        sorted_data = sorted(merged.values(), key=lambda x: x.get('calculated_rating', 0), reverse=True)

        for data in sorted_data:
            artist = data.get('artist', 'Unknown Artist')
            title = data.get('title', 'Unknown Title')
            rating = data.get('rating', 0)
            good_votes = data.get('good_votes', 0)
            bad_votes = data.get('bad_votes', 0)
            skips = data.get('skips', 0)
            calculated_rating = data.get('calculated_rating', 0)
            # estools.set_rating(artist, title, calculated_rating)

            
            print(Fore.YELLOW + f"{artist} - {title}")
            print(Fore.CYAN + f"Rating: {rating}, Good Votes: {good_votes}, Bad Votes: {bad_votes}, Skips: {skips}")
            print(Fore.GREEN + f"Calculated Rating: {calculated_rating}")
            print(Style.RESET_ALL)

    global calculated_ratings
    calculated_ratings = merged

def get_calculated_rating(artist, title):
    """Called for each line during playlist creation to get the calculated rating."""
    global calculated_ratings
    if calculated_ratings is None:
        calculate_all_ratings(verbose=False)
    
    if calculated_ratings is not None:
        key = (artist, title)
        data = calculated_ratings.get(key)
        if data:
            return data.get('calculated_rating', 50)
    return 50

# The 3 print functions below are used to print the ratings, skips, and votes from the database.
# They are primarily used for debugging and manual inspection of the ratings data.
def print_skips():
    """Print all skips from the database."""
    dbh, sth = get_db_connection()

    sth.execute("SELECT * FROM apollo_skip")
    ratings = sth.fetchall()
    for rating in ratings:
        id = rating['id']
        artist = rating['artist']
        title = rating['title']
        album = rating['album']
        rating = rating['rating']
        

        # print id in yellow
        print(Fore.CYAN + f"{id}")
        print(Fore.YELLOW + f"  {artist} - {title}")
        print(Fore.WHITE + f"  Album: {album}")

        if(rating == "good"):
            print(Fore.GREEN + f"  {rating}")
        elif(rating == "bad"):
            print(Fore.RED + f"  {rating}")
        else:
            print(Fore.CYAN + f"  {rating}")
            
        
        print(Style.RESET_ALL)
            
    return

def print_votes():
    """Print all votes from the database."""
    dbh, sth = get_db_connection()

    sth.execute("SELECT * FROM apollo_vote")
    ratings = sth.fetchall()
    for rating in ratings:
        id = rating['id']
        artist = rating['artist']
        title = rating['title']
        album = rating['album']
        rating = rating['rating']
        

        # print id in yellow
        print(Fore.CYAN + f"{id}")
        print(Fore.YELLOW + f"  {artist} - {title}")
        print(Fore.WHITE + f"  Album: {album}")

        if(rating == "good"):
            print(Fore.GREEN + f"  {rating}")
        elif(rating == "bad"):
            print(Fore.RED + f"  {rating}")
        else:
            print(Fore.CYAN + f"  {rating}")
            
        
        print(Style.RESET_ALL)

def print_ratings():
    """Print all ratings from the database."""
    dbh, sth = get_db_connection()

    sth.execute("SELECT * FROM apollo_rating")
    rows = sth.fetchall()
    for row in rows:
        id = row['id'] if 'id' in row else None
        artist = row['artist']
        title = row['title']
        album = row.get('album', '') if 'album' in row else ''
        rating = row['rating']

        if id is not None:
            print(Fore.CYAN + f"{id}")
        print(Fore.YELLOW + f"  {artist} - {title}")
        if album:
            print(Fore.WHITE + f"  Album: {album}")
        print(Fore.GREEN + f"  Rating: {rating}")
        print(Style.RESET_ALL)