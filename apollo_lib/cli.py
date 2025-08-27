import argparse
from apollo_lib import playlist, scanner, ratings, compare

def main():
    """CLI entrypoint for Apollo playlist and library management."""
    # create subparser for (create, publish, scan)
    parser = argparse.ArgumentParser(description="Manage playlists in Apollo")
    subparsers = parser.add_subparsers(dest="command", required=True)
    # create subparser for create-playlist
    create_parser = subparsers.add_parser("create", help="Create a playlist")
    create_parser.add_argument("-t", "--type", required=True, choices=["ai", "artist", "path", "any"], help="Type of playlist to create")
    create_parser.add_argument("-i", "--input", required=True, help="Input string for the playlist type")
    create_parser.add_argument("-d", "--dynamic", action="store_true", help="Write dynamic M3U to DYNAMIC_PLAYLIST_FILE")
    create_parser.add_argument("-p", "--playlist", help="Override DEFAULT_PLAYLIST_FILE for appending new tracks")
    create_parser.add_argument("-y", "--yes", action="store_true", help="Automatically answer yes to adding tracks to the default playlist")
    create_parser.set_defaults(func=handle_playlist)
    
    # create subparser for publish-playlist
    publish_parser = subparsers.add_parser("publish", help="Publish playlists")
    publish_group = publish_parser.add_mutually_exclusive_group(required=True)
    publish_group.add_argument("-a", "--all", action="store_true", help="Publish all playlists")
    publish_group.add_argument("-p", "--playlist", metavar="NAME", help="Publish single playlist by name (without .txt)")
    publish_parser.set_defaults(func=handle_publish)

    # create subparser for scan-music
    scan_parser = subparsers.add_parser("scan", help="Scan music folder into Elasticsearch")
    scan_parser.set_defaults(func=handle_scan)

    # compare
    compare_parser = subparsers.add_parser("compare", help="Compare a directory of mp3s with ES and list better versions")
    compare_parser.add_argument("-d", "--directory", required=True, help="Directory to compare")
    compare_parser.set_defaults(func=handle_compare)

    # create a subparser for rating [(-ps, --printskips), (-pv, --printvotes), (-pr, --printratings), (-c, --calc)+(-a, --artist)+(-t, --title)]
    rating_parser = subparsers.add_parser("rating", help="Manage ratings")
    rating_group = rating_parser.add_mutually_exclusive_group(required=True)
    rating_group.add_argument("-ps", "--printskips", action="store_true", help="Print all skips")
    rating_group.add_argument("-pv", "--printvotes", action="store_true", help="Print all votes")
    rating_group.add_argument("-pr", "--printratings", action="store_true", help="Print all ratings")
    rating_group.add_argument("-ca", "--calculate-all", action="store_true", help="Calculate ratings for all songs")
    rating_group.add_argument("-c", "--calc", action="store_true", help="Calculate rating for a song")
    rating_parser.add_argument("-a", "--artist", type=str, help="Artist name for calculation")
    rating_parser.add_argument("-t", "--title", type=str, help="Title of the song for calculation")
    rating_parser.add_argument("-v", "--verbose", action="store_true", help="Print verbose output")
    rating_parser.set_defaults(func=handle_rating)
    
    args = parser.parse_args()
    
    if args.command == "rating" and args.calc:
        if not args.artist or not args.title:
            parser.error("rating -c requires -a/--artist and -t/--title")
    
    args.func(args)

def handle_playlist(args):
    """Handle 'create' command and build playlists."""
    input = args.input.replace('"', '')
    ptype = args.type
    playlist.create_playlist(ptype=ptype, input_str=input, dynamic=args.dynamic, default_playlist_file=args.playlist, auto_yes=args.yes)

def handle_publish(args):
    """Handle 'publish' command to write M3U files."""
    if args.all:
        playlist.write_m3u_files(None)
    else:
        name = args.playlist
        if not name.endswith(".txt"):
            name = name + ".txt"
        playlist.write_m3u_files(name)

def handle_scan(args):
    """Handle 'scan' command to index music into ES."""
    scanner.scan_music_folder_into_es()

def handle_compare(args):
    """Handle 'compare' command to find better versions."""
    compare.compare_directory(args.directory)

def handle_rating(args):
    """Handle 'rating' command operations."""
    if args.printskips:
        ratings.print_skips()
    elif args.printvotes:
        ratings.print_votes()
    elif args.printratings:
        ratings.print_ratings()
    elif args.printratings:
        ratings.print_ratings()
    elif args.calculate_all:
        ratings.calculate_all_ratings(verbose=args.verbose)
    elif args.calc and args.artist and args.title:
        ratings.calculate_rating(args.artist, args.title)
    else:
        print("Invalid rating command. Use -h for help.")


if __name__ == "__main__":
    main()