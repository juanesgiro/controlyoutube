#!/usr/bin/env python3
"""
Music Remote para Windows.
Ejecuta: python server-windows.py
En la PC abre: http://localhost:8899/player
En el celular abre: http://<tu-ip>:8899
"""

import http.server
import json
import urllib.parse
import ctypes
import threading

PORT = 8899

# Estado compartido entre el celular (remote) y la PC (player)
state = {
    "mode": "",           # "mi" o "cl"
    "url_mi": "",
    "url_cl": "",
    "command": "",        # "play", "pause", "next", "prev"
    "command_id": 0,
    "direct_url": "",     # URL directa para reproducir
    "direct_url_id": 0,   # ID para detectar cambios
}
lock = threading.Lock()

# Volume keys
VK_MEDIA_PLAY_PAUSE = 0xB3
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002

def set_volume_precise(value):
    try:
        import subprocess
        v = max(0, min(100, int(value))) / 100.0
        ps2 = f"""
Add-Type -TypeDefinition @'
using System.Runtime.InteropServices;
[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume {{
    int _0(); int _1(); int _2(); int _3(); int _4(); int _5(); int _6();
    int SetMasterVolumeLevelScalar(float fLevel, System.Guid pguidEventContext);
    int GetMasterVolumeLevelScalar(out float pfLevel);
}}
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice {{ int Activate(ref System.Guid id, int clsCtx, int a, out IAudioEndpointVolume aev); }}
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator {{ int GetDefaultAudioEndpoint(int flow, int role, out IMMDevice dev); }}
[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDevEn {{}}
public class Vol {{
    public static void Set(float l) {{
        var e = (IMMDeviceEnumerator)(new MMDevEn());
        e.GetDefaultAudioEndpoint(0, 1, out IMMDevice d);
        var iid = typeof(IAudioEndpointVolume).GUID;
        d.Activate(ref iid, 1, 0, out IAudioEndpointVolume v);
        v.SetMasterVolumeLevelScalar(l, System.Guid.Empty);
    }}
}}
'@
[Vol]::Set({v})
"""
        subprocess.run(["powershell", "-Command", ps2], capture_output=True, timeout=5)
    except Exception:
        pass

def get_volume():
    try:
        import subprocess
        ps = """
Add-Type -TypeDefinition @'
using System.Runtime.InteropServices;
[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume {
    int _0(); int _1(); int _2(); int _3(); int _4(); int _5(); int _6();
    int SetMasterVolumeLevelScalar(float fLevel, System.Guid pguidEventContext);
    int GetMasterVolumeLevelScalar(out float pfLevel);
}
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice { int Activate(ref System.Guid id, int clsCtx, int a, out IAudioEndpointVolume aev); }
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator { int GetDefaultAudioEndpoint(int flow, int role, out IMMDevice dev); }
[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDevEn {}
public class Vol {
    public static float Get() {
        var e = (IMMDeviceEnumerator)(new MMDevEn());
        e.GetDefaultAudioEndpoint(0, 1, out IMMDevice d);
        var iid = typeof(IAudioEndpointVolume).GUID;
        d.Activate(ref iid, 1, 0, out IAudioEndpointVolume v);
        v.GetMasterVolumeLevelScalar(out float l);
        return l;
    }
}
'@
[math]::Round([Vol]::Get() * 100)
"""
        r = subprocess.run(["powershell", "-Command", ps], capture_output=True, text=True, timeout=5)
        return int(r.stdout.strip())
    except Exception:
        return 50


def extract_video_id(url):
    """Extrae el video ID de un link de YouTube."""
    if "youtu.be/" in url:
        return url.split("youtu.be/")[1].split("?")[0].split("&")[0]
    if "youtube.com" in url:
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        if "v" in params:
            return params["v"][0]
        if "list" in params:
            return None  # playlist
    return None

def extract_playlist_id(url):
    """Extrae el playlist ID de un link de YouTube."""
    if "youtube.com" in url:
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        if "list" in params:
            return params["list"][0]
    return None


# ==================== PLAYER PAGE (corre en el navegador de la PC) ====================
PLAYER_HTML = r"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<title>Music Player</title>
<style>
  body { background: #111; color: #fff; font-family: sans-serif; display: flex;
         flex-direction: column; align-items: center; justify-content: center;
         height: 100vh; margin: 0; }
  h1 { color: #555; font-size: 1.2em; letter-spacing: 2px; margin-bottom: 20px; }
  #status { color: #666; font-size: 0.9em; margin-top: 10px; }
  #players { display: flex; gap: 20px; }
  .player-box { text-align: center; }
  .player-box .label { color: #666; font-size: 0.8em; margin-bottom: 8px; }
  iframe { border-radius: 8px; }
  .active-label { color: #1db954; font-weight: bold; }
</style>
</head>
<body>
<h1>MUSIC PLAYER</h1>
<p id="status">Esperando comandos del celular...</p>

<div id="players">
  <div class="player-box">
    <div class="label" id="label-mi">Mi Musica</div>
    <div id="container-mi"><div id="yt-mi"></div></div>
  </div>
  <div class="player-box">
    <div class="label" id="label-cl">Clientes</div>
    <div id="container-cl"><div id="yt-cl"></div></div>
  </div>
</div>

<script>
let playerMi = null;
let playerCl = null;
let playerMiReady = false;
let playerClReady = false;
let currentMode = '';
let lastCommandId = 0;
let lastDirectId = 0;
let urlMi = '';
let urlCl = '';
let pendingMode = '';
let apiReady = false;

// Load YouTube IFrame API
const tag = document.createElement('script');
tag.src = "https://www.youtube.com/iframe_api";
document.head.appendChild(tag);

function onYouTubeIframeAPIReady() {
  apiReady = true;
  document.getElementById('status').textContent = 'YouTube listo. Pega links en el celular.';
}

function getVideoId(url) {
  if (!url) return null;
  let m = url.match(/youtu\.be\/([^?&]+)/);
  if (m) return m[1];
  m = url.match(/[?&]v=([^&]+)/);
  if (m) return m[1];
  return null;
}

function getPlaylistId(url) {
  if (!url) return null;
  let m = url.match(/[?&]list=([^&]+)/);
  if (m) return m[1];
  return null;
}

function loadPlayer(which, url) {
  if (!apiReady) return;
  const videoId = getVideoId(url);
  const playlistId = getPlaylistId(url);

  const opts = {
    height: '250',
    width: '350',
    playerVars: { autoplay: 0, controls: 1, enablejsapi: 1, origin: window.location.origin },
    events: {
      onReady: function(e) {
        if (which === 'mi') {
          playerMiReady = true;
          if (pendingMode === 'mi') switchMode('mi');
        } else {
          playerClReady = true;
          if (pendingMode === 'cl') switchMode('cl');
        }
      }
    }
  };

  if (playlistId) {
    opts.playerVars.listType = 'playlist';
    opts.playerVars.list = playlistId;
  } else if (videoId) {
    opts.videoId = videoId;
  }

  if (which === 'mi') {
    if (playerMi) { try { playerMi.destroy(); } catch(e) {} }
    playerMiReady = false;
    document.getElementById('container-mi').innerHTML = '<div id="yt-mi"></div>';
    playerMi = new YT.Player('yt-mi', opts);
  } else {
    if (playerCl) { try { playerCl.destroy(); } catch(e) {} }
    playerClReady = false;
    document.getElementById('container-cl').innerHTML = '<div id="yt-cl"></div>';
    playerCl = new YT.Player('yt-cl', opts);
  }
}

function switchMode(mode) {
  pendingMode = mode;

  if (mode === 'mi') {
    if (playerCl && playerClReady) {
      try { playerCl.pauseVideo(); } catch(e) {}
    }
    if (playerMi && playerMiReady) {
      try { playerMi.playVideo(); } catch(e) {}
      currentMode = mode;
    }
    document.getElementById('label-mi').className = 'label active-label';
    document.getElementById('label-cl').className = 'label';
  } else if (mode === 'cl') {
    if (playerMi && playerMiReady) {
      try { playerMi.pauseVideo(); } catch(e) {}
    }
    if (playerCl && playerClReady) {
      try { playerCl.playVideo(); } catch(e) {}
      currentMode = mode;
    }
    document.getElementById('label-cl').className = 'label active-label';
    document.getElementById('label-mi').className = 'label';
  }
  document.getElementById('status').textContent = 'Modo: ' + (mode === 'mi' ? 'Mi Musica' : 'Clientes');
}

function getActivePlayer() {
  if (currentMode === 'mi' && playerMiReady) return playerMi;
  if (currentMode === 'cl' && playerClReady) return playerCl;
  if (playerMiReady) return playerMi;
  if (playerClReady) return playerCl;
  return null;
}

function playDirectUrl(url) {
  if (!apiReady) return;
  const videoId = getVideoId(url);
  const playlistId = getPlaylistId(url);

  // Pause both players first
  if (playerMi && playerMiReady) try { playerMi.pauseVideo(); } catch(e) {}
  if (playerCl && playerClReady) try { playerCl.pauseVideo(); } catch(e) {}

  // Load in "mi" player slot
  const opts = {
    height: '250',
    width: '350',
    playerVars: { autoplay: 1, controls: 1, enablejsapi: 1, origin: window.location.origin },
    events: {
      onReady: function(e) {
        playerMiReady = true;
        currentMode = 'mi';
        e.target.playVideo();
        document.getElementById('label-mi').className = 'label active-label';
        document.getElementById('label-cl').className = 'label';
        document.getElementById('status').textContent = 'Reproduciendo...';
      }
    }
  };

  if (playlistId) {
    opts.playerVars.listType = 'playlist';
    opts.playerVars.list = playlistId;
  } else if (videoId) {
    opts.videoId = videoId;
  }

  if (playerMi) { try { playerMi.destroy(); } catch(e) {} }
  playerMiReady = false;
  document.getElementById('container-mi').innerHTML = '<div id="yt-mi"></div>';
  playerMi = new YT.Player('yt-mi', opts);
}

function executeCommand(cmd) {
  const p = getActivePlayer();
  if (!p) return;
  try {
    if (cmd === 'play') p.playVideo();
    else if (cmd === 'pause') p.pauseVideo();
    else if (cmd === 'play-pause') {
      const st = p.getPlayerState();
      if (st === 1) p.pauseVideo();
      else p.playVideo();
    }
    else if (cmd === 'next') p.nextVideo();
    else if (cmd === 'prev') p.previousVideo();
  } catch(e) {}
}

// Poll server for commands
async function poll() {
  try {
    const r = await fetch('/state');
    const d = await r.json();

    if (d.url_mi && d.url_mi !== urlMi) {
      urlMi = d.url_mi;
      loadPlayer('mi', urlMi);
    }
    if (d.url_cl && d.url_cl !== urlCl) {
      urlCl = d.url_cl;
      loadPlayer('cl', urlCl);
    }

    if (d.mode && d.mode !== currentMode) {
      switchMode(d.mode);
    }

    if (d.direct_url && d.direct_url_id > lastDirectId) {
      lastDirectId = d.direct_url_id;
      playDirectUrl(d.direct_url);
    }

    if (d.command && d.command_id > lastCommandId) {
      lastCommandId = d.command_id;
      executeCommand(d.command);
    }

  } catch(e) {}
}

setInterval(poll, 500);
</script>
</body></html>
"""


# ==================== REMOTE PAGE (corre en el celular) ====================
REMOTE_HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<title>Music Remote</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, system-ui, sans-serif;
    background: #0a0a0a;
    color: #fff;
    min-height: 100dvh;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 30px 16px;
    user-select: none;
    -webkit-user-select: none;
  }
  #title {
    font-size: 1.1em;
    color: #555;
    margin-bottom: 20px;
    letter-spacing: 2px;
    text-transform: uppercase;
  }

  /* --- Controles play/pause --- */
  .controls {
    display: flex;
    align-items: center;
    gap: 24px;
    margin-bottom: 24px;
  }
  .btn {
    background: none;
    border: none;
    color: #fff;
    cursor: pointer;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: transform 0.1s, background 0.2s;
    -webkit-tap-highlight-color: transparent;
  }
  .btn:active { transform: scale(0.9); background: rgba(255,255,255,0.1); }
  .btn-large { width: 80px; height: 80px; background: #fff; color: #0a0a0a; }
  .btn-large:active { background: #ccc; }
  .btn svg { width: 28px; height: 28px; }
  .btn-large svg { width: 36px; height: 36px; }
  .now-playing {
    color: #888;
    font-size: 0.85em;
    margin-bottom: 8px;
    min-height: 20px;
    text-align: center;
  }

  /* --- Canciones guardadas --- */
  .section-label {
    font-size: 0.7em;
    color: #555;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    margin-bottom: 10px;
    width: 100%;
    max-width: 340px;
  }
  .songs-list {
    width: 100%;
    max-width: 340px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin-bottom: 20px;
  }
  .song-btn {
    display: flex;
    align-items: center;
    padding: 14px 16px;
    border-radius: 12px;
    border: 1px solid #222;
    background: #111;
    color: #fff;
    font-size: 1em;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.15s;
    -webkit-tap-highlight-color: transparent;
    text-align: left;
    gap: 12px;
  }
  .song-btn:active { background: #1db954; border-color: #1db954; transform: scale(0.98); }
  .song-btn.playing { border-color: #1db954; background: rgba(29,185,84,0.15); }
  .song-btn .song-icon {
    width: 36px; height: 36px;
    border-radius: 8px;
    background: #222;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }
  .song-btn.playing .song-icon { background: #1db954; }
  .song-btn .song-icon svg { width: 18px; height: 18px; }
  .song-btn .song-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .song-btn .song-delete {
    color: #444;
    padding: 4px 8px;
    font-size: 1.2em;
    border: none;
    background: none;
    cursor: pointer;
  }
  .song-btn .song-delete:active { color: #e74c3c; }

  /* --- Agregar cancion --- */
  .add-section {
    width: 100%;
    max-width: 340px;
    margin-bottom: 24px;
  }
  .add-row {
    display: flex;
    gap: 8px;
    margin-bottom: 6px;
  }
  .add-row input {
    flex: 1;
    padding: 10px 12px;
    border-radius: 10px;
    border: 1px solid #333;
    background: #111;
    color: #fff;
    font-size: 0.9em;
    outline: none;
  }
  .add-row input:focus { border-color: #555; }
  .add-btn {
    padding: 10px 16px;
    border-radius: 10px;
    border: none;
    background: #1db954;
    color: #fff;
    font-size: 0.9em;
    font-weight: 600;
    cursor: pointer;
  }
  .add-btn:active { background: #17a248; }

  /* --- Volumen --- */
  .volume {
    margin-top: 10px;
    display: flex;
    align-items: center;
    gap: 14px;
    width: 280px;
  }
  .volume svg { width: 22px; height: 22px; color: #666; flex-shrink: 0; }
  input[type=range] {
    -webkit-appearance: none;
    appearance: none;
    flex: 1;
    height: 6px;
    border-radius: 3px;
    background: #333;
    outline: none;
  }
  input[type=range]::-webkit-slider-thumb {
    -webkit-appearance: none;
    width: 22px; height: 22px;
    border-radius: 50%;
    background: #fff;
    cursor: pointer;
  }
  input[type=range]::-moz-range-thumb {
    width: 22px; height: 22px;
    border-radius: 50%;
    background: #fff;
    cursor: pointer;
    border: none;
  }
  #vol-label {
    color: #666;
    font-size: 0.85em;
    min-width: 35px;
    text-align: center;
  }
  .status {
    margin-top: 16px;
    color: #333;
    font-size: 0.7em;
  }
</style>
</head>
<body>

<div id="title">Music Remote</div>

<!-- Now playing + Play/Pause -->
<div class="now-playing" id="now-playing"></div>

<div class="controls">
  <button class="btn btn-large" onclick="sendCmd('play-pause')">
    <svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 19h4V5H6zm8-14v14h4V5z"/></svg>
  </button>
</div>

<!-- Canciones guardadas -->
<div class="section-label">Canciones</div>
<div class="songs-list" id="songs-list"></div>

<!-- Agregar cancion -->
<div class="add-section">
  <div class="add-row">
    <input type="text" id="add-name" placeholder="Nombre...">
  </div>
  <div class="add-row">
    <input type="url" id="add-url" placeholder="Link de YouTube...">
    <button class="add-btn" onclick="addSong()">+</button>
  </div>
</div>

<!-- Volumen -->
<div class="volume">
  <svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02z"/></svg>
  <input type="range" id="vol" min="0" max="100" value="50"
    oninput="document.getElementById('vol-label').textContent=this.value+'%'; sendVol(this.value)">
  <span id="vol-label">50%</span>
</div>

<div class="status" id="status">Conectando...</div>

<script>
let volTimeout;
let currentPlaying = -1; // index of currently playing song

function getSongs() {
  try { return JSON.parse(localStorage.getItem('songs') || '[]'); } catch(e) { return []; }
}

function saveSongs(songs) {
  localStorage.setItem('songs', JSON.stringify(songs));
}

function renderSongs() {
  const list = document.getElementById('songs-list');
  const songs = getSongs();
  list.innerHTML = '';
  songs.forEach((song, i) => {
    const div = document.createElement('button');
    div.className = 'song-btn' + (i === currentPlaying ? ' playing' : '');
    div.innerHTML = `
      <div class="song-icon">
        <svg viewBox="0 0 24 24" fill="currentColor">
          ${i === currentPlaying
            ? '<path d="M6 19h4V5H6zm8-14v14h4V5z"/>'
            : '<path d="M8 5v14l11-7z"/>'}
        </svg>
      </div>
      <span class="song-name">${song.name}</span>
    `;
    div.onclick = (e) => {
      if (e.target.closest('.song-delete')) return;
      playSong(i);
    };
    // Long press to delete
    let holdTimer;
    div.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      if (confirm('Borrar "' + song.name + '"?')) {
        deleteSong(i);
      }
    });
    list.appendChild(div);
  });
}

function addSong() {
  const name = document.getElementById('add-name').value.trim();
  const url = document.getElementById('add-url').value.trim();
  if (!name || !url) {
    alert('Escribe un nombre y pega un link de YouTube');
    return;
  }
  const songs = getSongs();
  songs.push({ name, url });
  saveSongs(songs);
  document.getElementById('add-name').value = '';
  document.getElementById('add-url').value = '';
  renderSongs();
}

function deleteSong(index) {
  const songs = getSongs();
  songs.splice(index, 1);
  saveSongs(songs);
  if (currentPlaying === index) currentPlaying = -1;
  else if (currentPlaying > index) currentPlaying--;
  renderSongs();
}

async function playSong(index) {
  const songs = getSongs();
  if (index < 0 || index >= songs.length) return;
  const song = songs[index];
  currentPlaying = index;
  document.getElementById('now-playing').textContent = song.name;
  renderSongs();
  try {
    await fetch('/api?action=play-direct&url=' + encodeURIComponent(song.url));
    document.getElementById('status').textContent = 'Conectado';
  } catch(e) {
    document.getElementById('status').textContent = 'Sin conexion';
  }
}

async function sendCmd(cmd) {
  try {
    await fetch('/api?action=command&cmd=' + cmd);
    document.getElementById('status').textContent = 'Conectado';
  } catch(e) {
    document.getElementById('status').textContent = 'Sin conexion';
  }
}

function sendVol(v) {
  clearTimeout(volTimeout);
  volTimeout = setTimeout(() => fetch('/api?action=volume&value=' + v), 200);
}

async function poll() {
  try {
    const r = await fetch('/api?action=status');
    const d = await r.json();
    if (d.volume !== undefined) {
      document.getElementById('vol').value = d.volume;
      document.getElementById('vol-label').textContent = d.volume + '%';
    }
    document.getElementById('status').textContent = 'Conectado';
  } catch(e) {
    document.getElementById('status').textContent = 'Sin conexion';
  }
}

window.onload = function() {
  renderSongs();
  poll();
};

setInterval(poll, 5000);
</script>
</body></html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/api":
            params = urllib.parse.parse_qs(parsed.query)
            action = params.get("action", ["status"])[0]

            with lock:
                if action == "set-url":
                    t = params.get("type", [""])[0]
                    url = params.get("url", [""])[0]
                    if t == "mi":
                        state["url_mi"] = url
                    elif t == "cl":
                        state["url_cl"] = url

                elif action == "switch-mode":
                    mode = params.get("mode", [""])[0]
                    state["mode"] = mode

                elif action == "play-direct":
                    url = params.get("url", [""])[0]
                    if url:
                        state["direct_url"] = url
                        state["direct_url_id"] += 1

                elif action == "command":
                    cmd = params.get("cmd", [""])[0]
                    state["command"] = cmd
                    state["command_id"] += 1

                elif action == "volume":
                    val = params.get("value", ["50"])[0]
                    set_volume_precise(val)

            data = {"ok": True}
            if action == "status":
                data["volume"] = get_volume()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

        elif parsed.path == "/state":
            with lock:
                data = dict(state)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

        elif parsed.path == "/player":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(PLAYER_HTML.encode())

        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(REMOTE_HTML.encode())


if __name__ == "__main__":
    import socket
    import webbrowser
    ip = ""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = "tu-ip"

    print(f"Music Remote corriendo!")
    print(f"")
    print(f"EN ESTA PC abre:    http://localhost:{PORT}/player")
    print(f"EN EL CELULAR abre: http://{ip}:{PORT}")
    print(f"")
    print(f"(Ctrl+C para detener)")

    # Abrir player solo si no hay argumento --no-open
    if "--no-open" not in sys.argv:
        import webbrowser
        webbrowser.open(f"http://localhost:{PORT}/player")

    server = http.server.HTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nApagado.")
        server.server_close()
