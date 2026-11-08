import subprocess, sys, time
from playwright.sync_api import sync_playwright
vid = sys.argv[1]
with sync_playwright() as p:
    b = p.chromium.launch(headless=False, args=["--autoplay-policy=no-user-gesture-required", "--start-fullscreen",
                                                 "--window-size=1920,1080", "--window-position=0,0", "--kiosk"])
    pg = b.new_page(viewport={"width": 1920, "height": 1080})
    pg.goto(f"https://www.youtube.com/watch?v={vid}", wait_until="domcontentloaded", timeout=60000)
    time.sleep(6)
    for sel in ['button[aria-label*="Accept"]', 'button[aria-label*="Aceitar"]', 'button:has-text("Accept all")']:
        try: pg.click(sel, timeout=2000); break
        except Exception: pass
    time.sleep(3)
    print("titulo:", pg.title())
    print("texto bot:", pg.locator("text=not a bot").count(), pg.locator("text=Sign in").count())
    info = pg.evaluate("""() => { const p = document.querySelector('#movie_player'); const v = document.querySelector('video');
      try { p.setPlaybackQualityRange('hd1080','hd1080'); p.setPlaybackQuality && p.setPlaybackQuality('hd1080'); } catch(e) {}
      if (v) { v.muted = true; v.play(); }
      return {tem_player: !!p, tem_video: !!v, qual: p && p.getAvailableQualityLevels ? p.getAvailableQualityLevels() : null}; }""")
    print("info:", info)
    time.sleep(8)
    pg.evaluate("""() => { const v = document.querySelector('video'); const s = document.createElement('style');
      s.textContent = 'video{position:fixed!important;inset:0!important;width:100vw!important;height:100vh!important;z-index:999999!important;background:#000;object-fit:contain} body{overflow:hidden}';
      document.head.append(s); document.body.append(v); }""")
    time.sleep(2)
    for k in range(3):
        st = pg.evaluate("""() => { const v = document.querySelector('video'); const p = document.querySelector('#movie_player');
          return {t: v && v.currentTime, w: v && v.videoWidth, h: v && v.videoHeight, pausado: v && v.paused,
                  q: p && p.getPlaybackQuality ? p.getPlaybackQuality() : null, anuncio: !!document.querySelector('.ad-showing')}; }""")
        print("estado:", st, flush=True)
        if k == 0:
            rec = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-framerate", "30", "-video_size", "1920x1080",
                                    "-i", ":99", "-t", "20", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", f"grav_{vid}.mp4"])
        time.sleep(8)
    rec.wait()
    pg.screenshot(path=f"shot_{vid}.png")
    b.close()
