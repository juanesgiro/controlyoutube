#!/usr/bin/env python3
import http.server, json, urllib.parse, threading, sys

PORT = 8899

state = {"url": "", "url_id": 0}
lock = threading.Lock()

# ==================== PLAYER (PC) ====================
PLAYER = r"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Player</title>
<style>
  body { background:#111; color:#fff; font-family:sans-serif;
         display:flex; flex-direction:column; align-items:center;
         justify-content:center; height:100vh; margin:0; }
  #msg { color:#666; font-size:1em; margin-bottom:20px; }
</style>
</head><body>
<div id="msg">Esperando...</div>
<div id="box"><div id="yt"></div></div>
<script>
let player = null, ready = false, lastId = 0;

const tag = document.createElement('script');
tag.src = "https://www.youtube.com/iframe_api";
document.head.appendChild(tag);

function onYouTubeIframeAPIReady() {
  document.getElementById('msg').textContent = 'Listo. Elige cancion en el celular.';
}

function vid(url) {
  let m = url.match(/youtu\.be\/([^?&]+)/);
  if (m) return m[1];
  m = url.match(/[?&]v=([^&]+)/);
  return m ? m[1] : null;
}

function plist(url) {
  let m = url.match(/[?&]list=([^&]+)/);
  return m ? m[1] : null;
}

function play(url) {
  const v = vid(url), p = plist(url);

  if (player && ready) {
    if (p) player.loadPlaylist({listType:'playlist', list:p});
    else if (v) player.loadVideoById(v);
    document.getElementById('msg').textContent = 'Reproduciendo...';
    return;
  }

  const opts = {
    height:'360', width:'640',
    playerVars:{autoplay:1, controls:1, enablejsapi:1, origin:location.origin},
    events:{ onReady: function(){ ready = true;
      document.getElementById('msg').textContent = 'Reproduciendo...'; }}
  };
  if (p) { opts.playerVars.listType='playlist'; opts.playerVars.list=p; }
  else if (v) { opts.videoId = v; }

  document.getElementById('box').innerHTML = '<div id="yt"></div>';
  player = new YT.Player('yt', opts);
}

setInterval(async function(){
  try {
    const r = await fetch('/state');
    const d = await r.json();
    if (d.url && d.url_id > lastId) {
      lastId = d.url_id;
      play(d.url);
    }
  } catch(e){}
}, 500);
</script>
</body></html>
"""

# ==================== REMOTE (CELULAR) ====================
REMOTE = r"""<!DOCTYPE html>
<html lang="es"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<title>Control</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,system-ui,sans-serif;background:#0a0a0a;color:#fff;
       min-height:100dvh;display:flex;flex-direction:column;align-items:center;
       padding:40px 16px;user-select:none;-webkit-user-select:none}
  h1{font-size:1em;color:#555;letter-spacing:2px;text-transform:uppercase;margin-bottom:30px}
  .songs{width:100%;max-width:360px;display:flex;flex-direction:column;gap:10px;margin-bottom:30px}
  .song{padding:18px 20px;border-radius:14px;border:1px solid #222;background:#111;
        color:#fff;font-size:1.1em;font-weight:600;cursor:pointer;
        -webkit-tap-highlight-color:transparent;transition:all .15s;text-align:left}
  .song:active{background:#1db954;border-color:#1db954;transform:scale(.97)}
  .add{width:100%;max-width:360px}
  .add input{width:100%;padding:12px;border-radius:10px;border:1px solid #333;
             background:#111;color:#fff;font-size:.9em;outline:none;margin-bottom:8px}
  .add input:focus{border-color:#555}
  .add-btn{width:100%;padding:14px;border-radius:10px;border:none;
           background:#1db954;color:#fff;font-size:1em;font-weight:600;cursor:pointer}
  .add-btn:active{background:#17a248}
  .sep{color:#333;font-size:.7em;text-transform:uppercase;letter-spacing:1px;margin:10px 0 16px}
</style>
</head><body>
<h1>Control Musica</h1>
<div class="songs" id="songs"></div>
<div class="sep">Agregar cancion</div>
<div class="add">
  <input type="text" id="name" placeholder="Nombre (ej: Reggaeton Mix)">
  <input type="url" id="url" placeholder="Link de YouTube">
  <button class="add-btn" onclick="add()">Guardar</button>
</div>

<script>
function get(){try{return JSON.parse(localStorage.getItem('s')||'[]')}catch(e){return[]}}
function save(s){localStorage.setItem('s',JSON.stringify(s))}

function render(){
  const el=document.getElementById('songs'), s=get();
  el.innerHTML='';
  s.forEach((song,i)=>{
    const b=document.createElement('button');
    b.className='song';
    b.textContent=song.name;
    b.onclick=()=>fetch('/api?action=play&url='+encodeURIComponent(song.url));
    b.addEventListener('contextmenu',e=>{
      e.preventDefault();
      if(confirm('Borrar "'+song.name+'"?')){s.splice(i,1);save(s);render()}
    });
    el.appendChild(b);
  });
}

function add(){
  const n=document.getElementById('name').value.trim();
  const u=document.getElementById('url').value.trim();
  if(!n||!u){alert('Pon nombre y link');return}
  const s=get();s.push({name:n,url:u});save(s);
  document.getElementById('name').value='';
  document.getElementById('url').value='';
  render();
}

render();
</script>
</body></html>
"""

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        if p.path == "/api":
            q = urllib.parse.parse_qs(p.query)
            a = q.get("action",[""])[0]
            if a == "play":
                url = q.get("url",[""])[0]
                if url:
                    with lock:
                        state["url"] = url
                        state["url_id"] += 1
            self.send_response(200)
            self.send_header("Content-Type","application/json")
            self.send_header("Access-Control-Allow-Origin","*")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        elif p.path == "/state":
            with lock: d = dict(state)
            self.send_response(200)
            self.send_header("Content-Type","application/json")
            self.end_headers()
            self.wfile.write(json.dumps(d).encode())
        elif p.path == "/player":
            self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(PLAYER.encode())
        else:
            self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(REMOTE.encode())

if __name__ == "__main__":
    import socket
    ip = ""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
    except: ip = "tu-ip"
    print(f"EN LA PC abre:      http://localhost:{PORT}/player")
    print(f"EN EL CELULAR abre: http://{ip}:{PORT}")
    print(f"(Ctrl+C para detener)")
    if "--no-open" not in sys.argv:
        import webbrowser
        webbrowser.open(f"http://localhost:{PORT}/player")
    srv = http.server.HTTPServer(("0.0.0.0", PORT), H)
    try: srv.serve_forever()
    except KeyboardInterrupt: print("\nApagado."); srv.server_close()
