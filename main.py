import os
import sys
import json
import logging
import socket
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog
from flask import Flask, jsonify, send_from_directory, render_template_string, request
from PIL import Image

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_FILE = SCRIPT_DIR / "config.json"
SONG_LIST_FILE = SCRIPT_DIR / "song_list.json"
RUN_COUNT_FILE = SCRIPT_DIR / "run_count.txt"
SONG_REQUESTS_FILE = SCRIPT_DIR / "song_requests.json"
LOGS_DIR = SCRIPT_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# ----------------------------------------------------------------------
# Setup logging with run count increment
# ----------------------------------------------------------------------
def get_run_count():
    if RUN_COUNT_FILE.exists():
        count = int(RUN_COUNT_FILE.read_text().strip())
    else:
        count = 0
    count += 1
    RUN_COUNT_FILE.write_text(str(count))
    return count

run_number = get_run_count()
log_filename = LOGS_DIR / f"{datetime.now().strftime('%Y-%m-%d')}_{run_number}-SystemLog.txt"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_filename, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Utility: Get LAN IP address
# ----------------------------------------------------------------------
def get_lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

# ----------------------------------------------------------------------
# Pre-conversion: convert all JPG/JPEG files to PNG in song folders
# ----------------------------------------------------------------------
def convert_all_jpg_to_png(base_path: Path, exclude_paths: list):
    logger.info("Starting pre-conversion of JPG/JPEG album art to PNG...")
    converted_count = 0
    for folder in find_song_folders(base_path, exclude_paths):
        for file in folder.iterdir():
            if not file.is_file():
                continue
            if file.suffix.lower() in ('.jpg', '.jpeg'):
                png_path = file.with_suffix('.png')
                if not png_path.exists():
                    try:
                        img = Image.open(file)
                        img.save(png_path, 'PNG')
                        logger.info(f"Converted: {file.relative_to(base_path)} -> {png_path.name}")
                        converted_count += 1
                    except Exception as e:
                        logger.error(f"Failed to convert {file}: {e}")
    logger.info(f"Pre-conversion complete. Converted {converted_count} files.")

# ----------------------------------------------------------------------
# Scan for Clone Hero songs
# ----------------------------------------------------------------------
def find_song_folders(root_path: Path, exclude_paths: list):
    for dirpath, dirnames, filenames in os.walk(root_path):
        current = Path(dirpath)
        rel = current.relative_to(root_path)
        rel_str = str(rel).replace('\\', '/')
        skip = False
        for excl in exclude_paths:
            excl_norm = excl.replace('\\', '/').strip('/')
            if rel_str.startswith(excl_norm) or rel_str == excl_norm:
                skip = True
                break
        if skip:
            continue
        if "song.ini" in filenames or "notes.mid" in filenames or "notes.chart" in filenames:
            yield current

def find_album_art(folder: Path, base_path: Path):
    base_names = ['album', 'cover', 'folder', 'artwork', 'front']
    for file in folder.iterdir():
        if not file.is_file():
            continue
        name_lower = file.name.lower()
        for base in base_names:
            if name_lower.startswith(base) and file.suffix.lower() == '.png':
                return str(file.relative_to(base_path)).replace('\\', '/')
    for file in folder.iterdir():
        if not file.is_file():
            continue
        name_lower = file.name.lower()
        for base in base_names:
            if name_lower.startswith(base) and file.suffix.lower() in ('.jpg', '.jpeg'):
                return str(file.relative_to(base_path)).replace('\\', '/')
    return None

def parse_song_folder(folder: Path, base_path: Path):
    name = folder.name
    if " - " in name:
        artist, title = name.split(" - ", 1)
    else:
        artist, title = name, ""
    album_rel = find_album_art(folder, base_path)
    return {
        "folder": str(folder.relative_to(base_path)).replace('\\', '/'),
        "artist": artist.strip(),
        "title": title.strip(),
        "album_art": album_rel
    }

def scan_library(base_path: Path, exclude_paths: list):
    logger.info(f"Scanning library at: {base_path}")
    if exclude_paths:
        logger.info(f"Excluding paths: {exclude_paths}")
    songs = []
    skipped_folders = []
    for folder in find_song_folders(base_path, exclude_paths):
        try:
            songs.append(parse_song_folder(folder, base_path))
        except Exception as e:
            logger.warning(f"Skipping {folder}: {e}")
            skipped_folders.append(str(folder))
    logger.info(f"Found {len(songs)} songs.")
    if skipped_folders:
        logger.info(f"Skipped {len(skipped_folders)} folders due to errors.")
    return songs

def load_or_scan_library():
    config = {}
    if CONFIG_FILE.exists():
        config = json.loads(CONFIG_FILE.read_text())

    library_path = config.get("library_path")
    if not library_path or not Path(library_path).exists():
        root = tk.Tk()
        root.withdraw()
        library_path = filedialog.askdirectory(title="Select your 'Clone Hero Songs' folder")
        root.destroy()
        if not library_path:
            logger.error("No folder selected. Exiting.")
            sys.exit(1)
        config["library_path"] = library_path
        if "port" not in config:
            config["port"] = 80
        if "exclude_paths" not in config:
            config["exclude_paths"] = []
        CONFIG_FILE.write_text(json.dumps(config, indent=2))
        logger.info(f"Config saved with library path: {library_path}")

    base = Path(config["library_path"])
    exclude_paths = config.get("exclude_paths", [])

    need_rescan = True
    if SONG_LIST_FILE.exists():
        cached_data = json.loads(SONG_LIST_FILE.read_text())
        if isinstance(cached_data, dict) and "_exclude_paths" in cached_data:
            cached_exclude = cached_data["_exclude_paths"]
            if cached_exclude == exclude_paths:
                songs = cached_data["songs"]
                need_rescan = False
                logger.info("Exclusion list unchanged, using cached song list.")
    if need_rescan:
        logger.info("Exclusion list changed or no cache found. Rescanning...")
        convert_all_jpg_to_png(base, exclude_paths)
        songs = scan_library(base, exclude_paths)
        cache_data = {
            "_exclude_paths": exclude_paths,
            "songs": songs
        }
        SONG_LIST_FILE.write_text(json.dumps(cache_data, indent=2))
        logger.info("Song list saved to JSON with exclusion info.")
    else:
        songs = cached_data["songs"]

    return base, songs, config

# ----------------------------------------------------------------------
# Flask app
# ----------------------------------------------------------------------
app = Flask(__name__)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

BASE_PATH = None
SONGS = []

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/songs')
def api_songs():
    return jsonify(SONGS)

@app.route('/api/request', methods=['POST'])
def submit_request():
    data = request.get_json()
    artist = data.get('artist', '').strip()
    title = data.get('title', '').strip()
    notes = data.get('notes', '').strip()[:200]
    if not artist and not title:
        return jsonify({"error": "At least one of Artist or Title is required."}), 400

    req_entry = {
        "timestamp": datetime.now().isoformat(),
        "artist": artist,
        "title": title,
        "notes": notes
    }

    requests_list = []
    if SONG_REQUESTS_FILE.exists():
        try:
            requests_list = json.loads(SONG_REQUESTS_FILE.read_text())
        except:
            requests_list = []
    requests_list.append(req_entry)
    SONG_REQUESTS_FILE.write_text(json.dumps(requests_list, indent=2))
    logger.info(f"New song request: {artist} - {title}")
    return jsonify({"status": "success"})

@app.route('/album/<path:rel_path>')
def serve_album(rel_path):
    rel_path = rel_path.replace('\\', '/')
    full_path = BASE_PATH / rel_path.replace('/', os.sep)
    if not full_path.exists():
        return "", 404
    return send_from_directory(BASE_PATH, rel_path)

# ----------------------------------------------------------------------
# HTML Template
# ----------------------------------------------------------------------
HTML_TEMPLATE = r'''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>Clone Hero Song Catalog</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #1a1a1a;
            color: #eee;
            padding: 100px 20px 20px 120px;
        }
        .header {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 15px;
            background: #2a2a2a;
            padding: 15px 20px 15px 130px;
            border-bottom: 2px solid #444;
            z-index: 99;
            box-shadow: 0 2px 10px rgba(0,0,0,0.5);
        }
        .alphabet-sidebar {
            position: fixed;
            top: 90px;
            left: 10px;
            width: 100px;
            bottom: 20px;
            display: flex;
            flex-direction: column;
            gap: 4px;
            overflow-y: auto;
            scrollbar-width: none;
            -ms-overflow-style: none;
            background: transparent;
            z-index: 100;
        }
        .alphabet-sidebar::-webkit-scrollbar { display: none; }
        .alphabet-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 4px;
        }
        .alphabet-sidebar button {
            width: 45px;
            height: 45px;
            border-radius: 10px;
            border: none;
            background: #2a2a2a;
            color: white;
            font-weight: bold;
            font-size: 16px;
            cursor: pointer;
            transition: background 0.2s, transform 0.1s;
            box-shadow: 0 2px 5px rgba(0,0,0,0.5);
        }
        .alphabet-sidebar button:hover {
            background: #0078d4;
            transform: scale(1.05);
        }
        .request-btn {
            width: 94px !important;
            margin-top: 8px;
            background: #5a2a8c !important;
            font-size: 14px !important;
        }
        .back-button {
            background: #0078d4;
            color: white;
            border: none;
            border-radius: 20px;
            padding: 10px 20px;
            font-size: 16px;
            font-weight: bold;
            cursor: pointer;
            transition: background 0.2s;
            margin-right: 10px;
        }
        .back-button:hover { background: #106ebe; }
        .search-box { flex: 2; min-width: 200px; }
        .search-box input {
            width: 100%;
            padding: 10px 15px;
            font-size: 16px;
            border: none;
            border-radius: 20px;
            background: #3a3a3a;
            color: white;
        }
        .sort-control { flex: 1; min-width: 150px; }
        .sort-control select {
            width: 100%;
            padding: 10px;
            font-size: 16px;
            border-radius: 20px;
            background: #3a3a3a;
            color: white;
            border: none;
        }
        .song-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
            gap: 15px;
        }
        .artist-card {
            background: #2a2a2a;
            border-radius: 8px;
            overflow: hidden;
            transition: transform 0.1s;
            cursor: pointer;
        }
        .artist-card:hover {
            transform: scale(1.02);
            background: #3a3a3a;
        }
        .song-card {
            background: #2a2a2a;
            border-radius: 8px;
            overflow: hidden;
            transition: transform 0.1s;
            cursor: pointer;
        }
        .song-card:hover {
            transform: scale(1.02);
            background: #3a3a3a;
        }
        .album-art {
            width: 100%;
            aspect-ratio: 1 / 1;
            object-fit: cover;
            background: #444;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #888;
        }
        .card-info { padding: 10px; }
        .card-primary {
            font-weight: bold;
            font-size: 1.1em;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .card-secondary {
            color: #ccc;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .artist-count {
            color: #aaa;
            font-size: 0.9em;
            margin-top: 4px;
        }
        .scroll-controls {
            position: fixed;
            bottom: 30px;
            right: 30px;
            display: flex;
            flex-direction: column;
            gap: 8px;
            z-index: 101;
        }
        .scroll-btn {
            width: 50px;
            height: 50px;
            border-radius: 12px;
            background: #5a2a8c;
            color: white;
            border: none;
            font-size: 24px;
            cursor: pointer;
            box-shadow: 0 4px 8px rgba(0,0,0,0.3);
            display: flex;
            align-items: center;
            justify-content: center;
            transition: background 0.2s;
        }
        .scroll-btn.small-text {
            font-size: 16px;
            font-weight: bold;
        }
        .scroll-btn:hover { background: #7a3aac; }
        .no-results {
            grid-column: 1 / -1;
            text-align: center;
            padding: 40px;
            color: #888;
            font-size: 1.2em;
        }
        .count-indicator {
            margin-left: auto;
            color: #aaa;
            font-size: 0.9em;
        }
        .view-container { display: block; }
        .hidden { display: none !important; }

        /* Request form - full width, fits screen */
        .request-form-container {
            width: 100%;
            height: calc(100vh - 140px);
            overflow-y: auto;
            padding: 10px 20px 20px 20px;
            background: transparent;
        }
        .request-form-container input,
        .request-form-container textarea {
            width: 100%;
            padding: 15px;
            margin-bottom: 15px;
            background: #2a2a2a;
            border: 2px solid #444;
            border-radius: 12px;
            color: white;
            font-size: 22px;
            font-family: inherit;
        }
        .request-form-container textarea {
            resize: vertical;
            min-height: 120px;
        }
        .form-buttons {
            display: flex;
            gap: 20px;
            justify-content: flex-end;
            margin-top: 15px;
            margin-bottom: 10px;
        }
        .form-btn {
            padding: 14px 35px;
            border: none;
            border-radius: 40px;
            font-size: 22px;
            font-weight: bold;
            cursor: pointer;
            transition: background 0.2s;
        }
        .form-btn.primary {
            background: #5a2a8c;
            color: white;
        }
        .form-btn.primary:hover { background: #7a3aac; }
        .form-btn.secondary {
            background: #555;
            color: white;
        }
        .form-btn.secondary:hover { background: #777; }
        .error-msg {
            color: #ff6b6b;
            margin-bottom: 10px;
            text-align: left;
            font-size: 18px;
        }

        /* On-screen keyboard */
        .osk-container {
            margin-top: 10px;
            background: #1e1e1e;
            border-radius: 12px;
            padding: 12px;
            width: 100%;
        }
        .osk-row {
            display: flex;
            gap: 8px;
            margin-bottom: 8px;
            justify-content: center;
        }
        .osk-key {
            flex: 1;
            min-width: 50px;
            height: 55px;
            background: #3a3a3a;
            border: none;
            border-radius: 10px;
            color: white;
            font-size: 24px;
            font-weight: bold;
            cursor: pointer;
            transition: background 0.1s;
        }
        .osk-key:hover { background: #5a5a5a; }
        .osk-key.special {
            background: #555;
            flex: 2;
        }
        .osk-key.space {
            flex: 5;
        }
    </style>
</head>
<body>
    <div class="alphabet-sidebar" id="alphabetSidebar">
        <div class="alphabet-grid" id="alphabetGrid"></div>
        <button class="request-btn" id="requestButton">📝 Request</button>
    </div>

    <!-- Main View -->
    <div id="mainView">
        <div class="header">
            <div class="search-box">
                <input type="text" id="searchInput" placeholder="Search artist or title..." autocomplete="off">
            </div>
            <div class="sort-control">
                <select id="sortSelect">
                    <option value="artist">Sort by Artist</option>
                    <option value="title">Sort by Title</option>
                </select>
            </div>
            <div class="count-indicator">
                <span id="songCount">0</span> songs
            </div>
        </div>
        <div id="songGrid" class="song-grid">
            <div class="no-results">Loading songs...</div>
        </div>
    </div>

    <!-- Artist Detail View -->
    <div id="artistDetailView" class="hidden">
        <div class="header">
            <button class="back-button" id="backToMainBtn">← Back</button>
            <div style="flex:1; font-size:1.5em; font-weight:bold;" id="artistDetailTitle"></div>
            <div class="count-indicator">
                <span id="artistSongCount">0</span> songs
            </div>
        </div>
        <div id="artistSongGrid" class="song-grid"></div>
    </div>

    <!-- Request View (full page) -->
    <div id="requestView" class="hidden">
        <div class="header">
            <button class="back-button" id="backFromRequestBtn">← Back</button>
            <div style="flex:1; font-size:1.5em; font-weight:bold;">Request a Song</div>
        </div>
        <div class="request-form-container">
            <div id="requestError" class="error-msg"></div>
            <input type="text" id="requestArtist" placeholder="Artist (optional)" class="osk-input">
            <input type="text" id="requestTitle" placeholder="Title (optional)" class="osk-input">
            <textarea id="requestNotes" placeholder="Notes (optional, max 200 chars)" maxlength="200" class="osk-input"></textarea>

            <!-- On-screen keyboard -->
            <div class="osk-container" id="osk">
                <div class="osk-row">
                    <button class="osk-key" data-char="1">1</button>
                    <button class="osk-key" data-char="2">2</button>
                    <button class="osk-key" data-char="3">3</button>
                    <button class="osk-key" data-char="4">4</button>
                    <button class="osk-key" data-char="5">5</button>
                    <button class="osk-key" data-char="6">6</button>
                    <button class="osk-key" data-char="7">7</button>
                    <button class="osk-key" data-char="8">8</button>
                    <button class="osk-key" data-char="9">9</button>
                    <button class="osk-key" data-char="0">0</button>
                </div>
                <div class="osk-row">
                    <button class="osk-key" data-char="q">Q</button>
                    <button class="osk-key" data-char="w">W</button>
                    <button class="osk-key" data-char="e">E</button>
                    <button class="osk-key" data-char="r">R</button>
                    <button class="osk-key" data-char="t">T</button>
                    <button class="osk-key" data-char="y">Y</button>
                    <button class="osk-key" data-char="u">U</button>
                    <button class="osk-key" data-char="i">I</button>
                    <button class="osk-key" data-char="o">O</button>
                    <button class="osk-key" data-char="p">P</button>
                </div>
                <div class="osk-row">
                    <button class="osk-key" data-char="a">A</button>
                    <button class="osk-key" data-char="s">S</button>
                    <button class="osk-key" data-char="d">D</button>
                    <button class="osk-key" data-char="f">F</button>
                    <button class="osk-key" data-char="g">G</button>
                    <button class="osk-key" data-char="h">H</button>
                    <button class="osk-key" data-char="j">J</button>
                    <button class="osk-key" data-char="k">K</button>
                    <button class="osk-key" data-char="l">L</button>
                </div>
                <div class="osk-row">
                    <button class="osk-key special" data-char="shift">⇧</button>
                    <button class="osk-key" data-char="z">Z</button>
                    <button class="osk-key" data-char="x">X</button>
                    <button class="osk-key" data-char="c">C</button>
                    <button class="osk-key" data-char="v">V</button>
                    <button class="osk-key" data-char="b">B</button>
                    <button class="osk-key" data-char="n">N</button>
                    <button class="osk-key" data-char="m">M</button>
                    <button class="osk-key special" data-char="backspace">⌫</button>
                </div>
                <div class="osk-row">
                    <button class="osk-key special" data-char="clear">Clear</button>
                    <button class="osk-key space" data-char=" ">Space</button>
                    <button class="osk-key special" data-char="enter">Enter</button>
                </div>
            </div>

            <div class="form-buttons">
                <button class="form-btn secondary" id="cancelRequest">Cancel</button>
                <button class="form-btn primary" id="submitRequest">Submit</button>
            </div>
        </div>
    </div>

    <div class="scroll-controls">
        <button class="scroll-btn" id="scrollUp" title="Scroll up">↑</button>
        <button class="scroll-btn" id="scrollDown" title="Scroll down">↓</button>
        <button class="scroll-btn small-text" id="backToTop" title="Back to top">Top</button>
    </div>

    <script>
        (function() {
            console.log("Script loaded");
            let allSongs = [];
            let filteredSongs = [];
            let currentSort = 'artist';
            let currentSearch = '';
            let currentView = 'main';
            let currentArtist = null;
            let mainScrollPosition = 0;
            let shiftActive = false;

            const CACHE_BUSTER = Date.now();

            function encodeAlbumPath(path) {
                return path.replace(/\\/g, '/').split('/').map(part => encodeURIComponent(part)).join('/');
            }

            async function loadSongs() {
                try {
                    const response = await fetch('/api/songs');
                    allSongs = await response.json();
                    applyFiltersAndSort();
                } catch (err) {
                    document.getElementById('songGrid').innerHTML = '<div class="no-results">Error loading songs. Check console.</div>';
                }
            }

            function applyFiltersAndSort() {
                const searchTerm = currentSearch.toLowerCase();
                filteredSongs = allSongs.filter(song => {
                    return song.artist.toLowerCase().includes(searchTerm) ||
                           song.title.toLowerCase().includes(searchTerm);
                });
                if (currentSort === 'artist') {
                    filteredSongs.sort((a, b) => a.artist.toLowerCase().localeCompare(b.artist.toLowerCase()));
                } else {
                    filteredSongs.sort((a, b) => a.title.toLowerCase().localeCompare(b.title.toLowerCase()));
                }
                updateMainView();
                if (currentView === 'main') {
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                }
            }

            function updateMainView() {
                if (currentView === 'main') {
                    renderGrid();
                    renderAlphabetSidebar();
                    document.getElementById('songCount').textContent = filteredSongs.length;
                }
            }

            function renderGrid() {
                const grid = document.getElementById('songGrid');
                if (filteredSongs.length === 0) {
                    grid.innerHTML = '<div class="no-results">No songs found</div>';
                    return;
                }
                if (currentSort === 'artist') {
                    renderArtistCards(grid);
                } else {
                    renderFlatSongCards(grid);
                }
            }

            function renderArtistCards(grid) {
                const groups = new Map();
                filteredSongs.forEach(song => {
                    if (!groups.has(song.artist)) groups.set(song.artist, []);
                    groups.get(song.artist).push(song);
                });
                const artists = Array.from(groups.keys()).sort((a,b) => a.toLowerCase().localeCompare(b.toLowerCase()));
                let html = '';
                artists.forEach(artist => {
                    const songs = groups.get(artist);
                    const firstSong = songs[0];
                    const albumUrl = firstSong.album_art ? '/album/' + encodeAlbumPath(firstSong.album_art) + '?v=' + CACHE_BUSTER : '';
                    html += `<div class="artist-card" data-artist="${artist.replace(/"/g, '&quot;')}">`;
                    if (albumUrl) {
                        html += `<img class="album-art" src="${albumUrl}" loading="lazy" onerror="this.onerror=null; this.parentNode.insertBefore(createFallbackDiv(), this); this.remove();">`;
                    } else {
                        html += '<div class="album-art">🎵</div>';
                    }
                    html += `<div class="card-info">`;
                    html += `<div class="card-primary" title="${artist.replace(/"/g, '&quot;')}">${artist}</div>`;
                    html += `<div class="artist-count">${songs.length} song${songs.length !== 1 ? 's' : ''}</div>`;
                    html += `</div></div>`;
                });
                grid.innerHTML = html;
                document.querySelectorAll('.artist-card').forEach(card => {
                    card.addEventListener('click', () => {
                        const artist = card.dataset.artist;
                        showArtistDetail(artist);
                    });
                });
            }

            function renderFlatSongCards(grid) {
                let html = '';
                filteredSongs.forEach(song => {
                    html += createSongCardHtml(song);
                });
                grid.innerHTML = html;
            }

            function createSongCardHtml(song) {
                const albumUrl = song.album_art ? '/album/' + encodeAlbumPath(song.album_art) + '?v=' + CACHE_BUSTER : '';
                let cardHtml = '<div class="song-card">';
                if (albumUrl) {
                    cardHtml += `<img class="album-art" src="${albumUrl}" loading="lazy" onerror="this.onerror=null; this.parentNode.insertBefore(createFallbackDiv(), this); this.remove();">`;
                } else {
                    cardHtml += '<div class="album-art">🎵</div>';
                }
                cardHtml += '<div class="card-info">';
                if (currentSort === 'artist') {
                    cardHtml += `<div class="card-primary" title="${song.artist.replace(/"/g, '&quot;')}">${song.artist}</div>`;
                    cardHtml += `<div class="card-secondary" title="${song.title.replace(/"/g, '&quot;')}">${song.title}</div>`;
                } else {
                    cardHtml += `<div class="card-primary" title="${song.title.replace(/"/g, '&quot;')}">${song.title}</div>`;
                    cardHtml += `<div class="card-secondary" title="${song.artist.replace(/"/g, '&quot;')}">${song.artist}</div>`;
                }
                cardHtml += '</div></div>';
                return cardHtml;
            }

            function createFallbackDiv() {
                const div = document.createElement('div');
                div.className = 'album-art';
                div.textContent = '🎵';
                return div;
            }

            function showArtistDetail(artist) {
                mainScrollPosition = window.scrollY;
                currentArtist = artist;
                currentView = 'artistDetail';
                document.getElementById('mainView').classList.add('hidden');
                document.getElementById('artistDetailView').classList.remove('hidden');
                document.getElementById('requestView').classList.add('hidden');
                document.getElementById('artistDetailTitle').textContent = artist;

                const artistSongs = filteredSongs.filter(s => s.artist === artist);
                document.getElementById('artistSongCount').textContent = artistSongs.length;

                const grid = document.getElementById('artistSongGrid');
                let html = '';
                artistSongs.sort((a,b) => a.title.toLowerCase().localeCompare(b.title.toLowerCase()));
                artistSongs.forEach(song => {
                    const albumUrl = song.album_art ? '/album/' + encodeAlbumPath(song.album_art) + '?v=' + CACHE_BUSTER : '';
                    html += '<div class="song-card">';
                    if (albumUrl) {
                        html += `<img class="album-art" src="${albumUrl}" loading="lazy" onerror="this.onerror=null; this.parentNode.insertBefore(createFallbackDiv(), this); this.remove();">`;
                    } else {
                        html += '<div class="album-art">🎵</div>';
                    }
                    html += '<div class="card-info">';
                    html += `<div class="card-primary" title="${song.title.replace(/"/g, '&quot;')}">${song.title}</div>`;
                    html += `<div class="card-secondary" title="${song.artist.replace(/"/g, '&quot;')}">${song.artist}</div>`;
                    html += '</div></div>';
                });
                grid.innerHTML = html;
                window.scrollTo({ top: 0 });
            }

            function backToMain() {
                currentView = 'main';
                currentArtist = null;
                document.getElementById('artistDetailView').classList.add('hidden');
                document.getElementById('requestView').classList.add('hidden');
                document.getElementById('mainView').classList.remove('hidden');
                window.scrollTo({ top: mainScrollPosition });
            }

            function showRequestView() {
                mainScrollPosition = window.scrollY;
                currentView = 'request';
                document.getElementById('mainView').classList.add('hidden');
                document.getElementById('artistDetailView').classList.add('hidden');
                document.getElementById('requestView').classList.remove('hidden');
                document.getElementById('requestArtist').value = '';
                document.getElementById('requestTitle').value = '';
                document.getElementById('requestNotes').value = '';
                document.getElementById('requestError').textContent = '';
                shiftActive = false;
                updateKeyboardCase();
                document.getElementById('requestArtist').focus();
                window.scrollTo({ top: 0 });
            }

            function renderAlphabetSidebar() {
                if (currentView !== 'main') return;
                const gridContainer = document.getElementById('alphabetGrid');
                const letters = new Set();
                filteredSongs.forEach(song => {
                    let val = currentSort === 'artist' ? song.artist : song.title;
                    val = val.trim();
                    if (val.length > 0) {
                        let first = val[0].toUpperCase();
                        if (!/[A-Z]/.test(first)) first = '#';
                        letters.add(first);
                    }
                });
                const sortedLetters = Array.from(letters).sort((a, b) => {
                    if (a === '#') return 1;
                    if (b === '#') return -1;
                    return a.localeCompare(b);
                });
                const half = Math.ceil(sortedLetters.length / 2);
                const col1 = sortedLetters.slice(0, half);
                const col2 = sortedLetters.slice(half);
                let html = '';
                for (let i = 0; i < Math.max(col1.length, col2.length); i++) {
                    if (i < col1.length) {
                        html += '<button data-letter="' + col1[i] + '">' + col1[i] + '</button>';
                    } else {
                        html += '<div></div>';
                    }
                    if (i < col2.length) {
                        html += '<button data-letter="' + col2[i] + '">' + col2[i] + '</button>';
                    } else {
                        html += '<div></div>';
                    }
                }
                gridContainer.innerHTML = html;
                gridContainer.querySelectorAll('button').forEach(btn => {
                    btn.addEventListener('click', function() {
                        const letter = this.dataset.letter;
                        let firstMatch = null;
                        if (currentSort === 'artist') {
                            const groups = new Map();
                            filteredSongs.forEach(s => groups.set(s.artist, true));
                            const artists = Array.from(groups.keys()).sort((a,b) => a.toLowerCase().localeCompare(b.toLowerCase()));
                            firstMatch = artists.find(artist => {
                                let val = artist.trim();
                                if (val.length === 0) return false;
                                let first = val[0].toUpperCase();
                                if (!/[A-Z]/.test(first)) first = '#';
                                return first === letter;
                            });
                            if (firstMatch) {
                                const cards = document.querySelectorAll('.artist-card');
                                for (let card of cards) {
                                    if (card.dataset.artist === firstMatch) {
                                        card.scrollIntoView({ behavior: 'smooth', block: 'start' });
                                        break;
                                    }
                                }
                            }
                        } else {
                            firstMatch = filteredSongs.find(song => {
                                let val = song.title.trim();
                                if (val.length === 0) return false;
                                let first = val[0].toUpperCase();
                                if (!/[A-Z]/.test(first)) first = '#';
                                return first === letter;
                            });
                            if (firstMatch) {
                                const index = filteredSongs.indexOf(firstMatch);
                                const cards = document.querySelectorAll('.song-card');
                                if (cards[index]) {
                                    cards[index].scrollIntoView({ behavior: 'smooth', block: 'start' });
                                }
                            }
                        }
                    });
                });
            }

            // --- On-screen keyboard logic ---
            const inputs = document.querySelectorAll('.osk-input');
            let currentInput = null;

            inputs.forEach(input => {
                input.addEventListener('focus', () => { currentInput = input; });
            });

            document.querySelectorAll('.osk-key').forEach(key => {
                key.addEventListener('click', (e) => {
                    if (!currentInput) {
                        currentInput = document.getElementById('requestArtist');
                        currentInput.focus();
                    }
                    const char = key.dataset.char;
                    if (char === 'backspace') {
                        currentInput.value = currentInput.value.slice(0, -1);
                    } else if (char === 'clear') {
                        currentInput.value = '';
                    } else if (char === 'enter') {
                        // Optional
                    } else if (char === 'shift') {
                        shiftActive = !shiftActive;
                        updateKeyboardCase();
                    } else if (char === ' ') {
                        insertAtCursor(currentInput, ' ');
                    } else {
                        let insertChar = char;
                        if (!shiftActive && char.length === 1 && /[A-Z]/.test(char)) {
                            insertChar = char.toLowerCase();
                        } else if (shiftActive && char.length === 1 && /[a-z]/.test(char)) {
                            insertChar = char.toUpperCase();
                        }
                        insertAtCursor(currentInput, insertChar);
                    }
                    currentInput.dispatchEvent(new Event('input', { bubbles: true }));
                });
            });

            function insertAtCursor(field, text) {
                const start = field.selectionStart;
                const end = field.selectionEnd;
                field.value = field.value.substring(0, start) + text + field.value.substring(end);
                field.selectionStart = field.selectionEnd = start + text.length;
                field.focus();
            }

            function updateKeyboardCase() {
                document.querySelectorAll('.osk-key[data-char]').forEach(key => {
                    let char = key.dataset.char;
                    if (char.length === 1 && /[A-Za-z]/.test(char)) {
                        if (shiftActive) {
                            key.textContent = char.toUpperCase();
                        } else {
                            key.textContent = char.toLowerCase();
                        }
                    }
                });
            }

            // Request view handling
            const requestBtn = document.getElementById('requestButton');
            const backFromRequestBtn = document.getElementById('backFromRequestBtn');
            const cancelRequestBtn = document.getElementById('cancelRequest');
            const submitBtn = document.getElementById('submitRequest');
            const artistInput = document.getElementById('requestArtist');
            const titleInput = document.getElementById('requestTitle');
            const notesInput = document.getElementById('requestNotes');
            const errorDiv = document.getElementById('requestError');

            requestBtn.addEventListener('click', showRequestView);
            backFromRequestBtn.addEventListener('click', backToMain);
            cancelRequestBtn.addEventListener('click', backToMain);

            submitBtn.addEventListener('click', async () => {
                const artist = artistInput.value.trim();
                const title = titleInput.value.trim();
                const notes = notesInput.value.trim();
                if (!artist && !title) {
                    errorDiv.textContent = 'Please enter at least an Artist or a Title.';
                    return;
                }
                try {
                    const response = await fetch('/api/request', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ artist, title, notes })
                    });
                    if (response.ok) {
                        backToMain();
                        alert('Request submitted! Thank you.');
                    } else {
                        const data = await response.json();
                        errorDiv.textContent = data.error || 'Submission failed.';
                    }
                } catch (err) {
                    errorDiv.textContent = 'Network error. Please try again.';
                }
            });

            // Other event listeners
            document.getElementById('searchInput').addEventListener('input', e => {
                currentSearch = e.target.value;
                applyFiltersAndSort();
            });
            document.getElementById('sortSelect').addEventListener('change', e => {
                currentSort = e.target.value;
                applyFiltersAndSort();
            });
            document.getElementById('backToMainBtn').addEventListener('click', backToMain);
            document.getElementById('scrollUp').addEventListener('click', () => window.scrollBy({ top: -250, behavior: 'smooth' }));
            document.getElementById('scrollDown').addEventListener('click', () => window.scrollBy({ top: 250, behavior: 'smooth' }));
            document.getElementById('backToTop').addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));

            loadSongs();
        })();
    </script>
</body>
</html>
'''

# ----------------------------------------------------------------------
# Main entry point
# ----------------------------------------------------------------------
if __name__ == '__main__':
    logger.info("=== Starting Clone Hero Song Catalog Server ===")
    BASE_PATH, SONGS, config = load_or_scan_library()

    HOST = get_lan_ip()
    PORT = config.get("port", 80)
    logger.info(f"Detected LAN IP: {HOST}")
    logger.info(f"Starting web server on http://{HOST}:{PORT}")

    print("\n" + "="*60)
    print(f"Server running at http://{HOST}:{PORT}")
    print("Access this URL from any device on the same network.")
    print("Press Ctrl+C to stop.")
    print("="*60 + "\n")

    app.run(host=HOST, port=PORT, debug=False, threaded=True)
