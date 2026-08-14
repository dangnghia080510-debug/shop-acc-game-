"""
TIKTOK TELEGRAM BOT v6
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Cài đặt:
  pip install pyTelegramBotAPI yt-dlp requests

Tính năng mới so với v5:
  /slide   - Tải slideshow ảnh + nhạc
  /hd      - Tải video chất lượng cao nhất
  /vinfo   - Xem thông tin video (không tải)
  /cut     - Cắt đoạn video (cần ffmpeg)
  /caption - Trích xuất phụ đề (cần ffmpeg)
  /history - Lịch sử link đã tải
  /watch   - Theo dõi TikToker (thông báo video mới)
  /unwatch - Bỏ theo dõi TikToker
  /watchlist - Danh sách đang theo dõi

Gỡ tính năng:
  /timvd   - (Đã bỏ theo yêu cầu)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import telebot
import yt_dlp
import requests
import os, re, time, random, threading, sqlite3, subprocess, shutil
from datetime import datetime
from urllib.parse import quote

# ══════════════════════════════════════════
#   CẤU HÌNH - SỬA Ở ĐÂY
# ══════════════════════════════════════════
BOT_TOKEN    = "8429160045:AAEboE_G_YkktPMS5TXCq0xAVCyyk5w5-AY"
DOWNLOAD_DIR = "/sdcard/TikTokBot"
MAX_FILE_MB  = 50
DB_PATH      = "/sdcard/TikTokBot/bot_data.db"
WATCH_INTERVAL = 600   # kiểm tra video mới mỗi 10 phút

bot = telebot.TeleBot(BOT_TOKEN)
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Xoay vòng nhiều User-Agent để đỡ bị nhận diện & chặn theo fingerprint cố định
USER_AGENT_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
]
def rand_ua():
    return random.choice(USER_AGENT_POOL)

def TIKTOK_HEADERS_R():
    return {
        "User-Agent": rand_ua(),
        "Referer": "https://www.tiktok.com/",
        "Accept-Language": "en-US,en;q=0.9",
    }

TIKTOK_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.tiktok.com/",
    "Accept-Language": "en-US,en;q=0.9",
}
TIKWM_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.tikwm.com/"}

batch_sessions = {}


# ══════════════════════════════════════════
#   DATABASE (SQLite)
# ══════════════════════════════════════════
def db_init():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    # Lịch sử tải
    cur.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   INTEGER,
            url       TEXT,
            title     TEXT,
            ts        INTEGER
        )
    """)
    # Danh sách theo dõi TikToker
    cur.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            chat_id     INTEGER,
            username    TEXT,
            last_vid_id TEXT,
            ts          INTEGER,
            UNIQUE(user_id, username)
        )
    """)
    con.commit()
    con.close()

def db_add_history(user_id, url, title):
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("INSERT INTO history (user_id, url, title, ts) VALUES (?,?,?,?)",
                    (user_id, url[:500], str(title)[:200], int(time.time())))
        # Giữ tối đa 50 lịch sử mỗi user
        con.execute("""
            DELETE FROM history WHERE user_id=? AND id NOT IN (
                SELECT id FROM history WHERE user_id=? ORDER BY ts DESC LIMIT 50
            )
        """, (user_id, user_id))
        con.commit()
        con.close()
    except: pass

def db_get_history(user_id, limit=10):
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute(
            "SELECT url, title, ts FROM history WHERE user_id=? ORDER BY ts DESC LIMIT ?",
            (user_id, limit)).fetchall()
        con.close()
        return rows
    except: return []

def db_add_watch(user_id, chat_id, username, last_vid_id=""):
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("""
            INSERT OR REPLACE INTO watchlist (user_id, chat_id, username, last_vid_id, ts)
            VALUES (?,?,?,?,?)
        """, (user_id, chat_id, username.lower(), last_vid_id, int(time.time())))
        con.commit()
        con.close()
        return True
    except: return False

def db_remove_watch(user_id, username):
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("DELETE FROM watchlist WHERE user_id=? AND username=?",
                    (user_id, username.lower()))
        con.commit()
        con.close()
        return True
    except: return False

def db_get_watchlist(user_id):
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute(
            "SELECT username FROM watchlist WHERE user_id=?", (user_id,)).fetchall()
        con.close()
        return [r[0] for r in rows]
    except: return []

def db_get_all_watches():
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute(
            "SELECT id, user_id, chat_id, username, last_vid_id FROM watchlist").fetchall()
        con.close()
        return rows
    except: return []

def db_update_last_vid(watch_id, last_vid_id):
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("UPDATE watchlist SET last_vid_id=? WHERE id=?",
                    (last_vid_id, watch_id))
        con.commit()
        con.close()
    except: pass

# ══════════════════════════════════════════
#   TIỆN ÍCH
# ══════════════════════════════════════════
def esc(text):
    if not text: return ""
    for ch in ["_", "*", "`", "["]:
        text = str(text).replace(ch, f"\\{ch}")
    return text

def fmt(n):
    try:
        n = int(n)
        if n >= 1_000_000_000: return f"{n/1_000_000_000:.1f}B"
        if n >= 1_000_000:     return f"{n/1_000_000:.1f}M"
        if n >= 1_000:         return f"{n/1_000:.1f}K"
        return str(n)
    except: return "0"

def clean_old():
    now = time.time()
    try:
        for f in os.listdir(DOWNLOAD_DIR):
            fp = os.path.join(DOWNLOAD_DIR, f)
            if os.path.isfile(fp) and now - os.path.getmtime(fp) > 3600:
                try: os.remove(fp)
                except: pass
    except: pass

def resolve_url(url):
    try:
        r = requests.head(url, allow_redirects=True, timeout=10,
                          headers={"User-Agent": "Mozilla/5.0"})
        return r.url
    except: return url

def dl_stream(video_url, filepath, timeout=90):
    try:
        headers = dict(TIKTOK_HEADERS)
        headers["User-Agent"] = rand_ua()
        r = requests.get(video_url, headers=headers,
                         timeout=timeout, stream=True)
        r.raise_for_status()
        # Chặn trường hợp link trả về trang HTML lỗi/chặn thay vì file video thật
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "text/html" in ctype or "application/json" in ctype:
            return False
        first_chunk = True
        with open(filepath, "wb") as f:
            for chunk in r.iter_content(65536):
                if not chunk: continue
                if first_chunk:
                    # File video thật không bắt đầu bằng "<" (html) hay "{" (json lỗi)
                    head = chunk[:20].lstrip()
                    if head[:1] in (b"<", b"{"):
                        try: f.close()
                        except: pass
                        try: os.remove(filepath)
                        except: pass
                        return False
                    first_chunk = False
                f.write(chunk)
        return os.path.exists(filepath) and os.path.getsize(filepath) > 10000
    except: return False

def has_ffmpeg():
    return shutil.which("ffmpeg") is not None

# ══════════════════════════════════════════
#   TIKWM API
# ══════════════════════════════════════════
def tikwm_get(url):
    for endpoint in ["https://www.tikwm.com/api/", "https://tikwm.com/api/"]:
        for _ in range(3):
            try:
                r = requests.post(endpoint,
                    data={"url": url, "count": 1, "cursor": 0, "web": 1, "hd": 1},
                    headers=TIKWM_HEADERS, timeout=20)
                d = r.json()
                if d.get("code") == 0 and d.get("data"):
                    return d["data"]
                time.sleep(1)
            except: time.sleep(1)
    return None

# ══════════════════════════════════════════
#   CÁC NGUỒN TẢI (mỗi nguồn 1 hàm — dễ thêm/bớt)
#   Mỗi hàm nhận (url, ts) và trả về dict kết quả hoặc None nếu thất bại
# ══════════════════════════════════════════
def _src_tikwm(url, ts):
    data = tikwm_get(url)
    if not data: return None
    vid_url = (data.get("play_addr") or data.get("wmplay") or
               data.get("hdplay")    or data.get("play"))
    fp = os.path.join(DOWNLOAD_DIR, f"tiktok_{ts}_tikwm.mp4")
    if vid_url and dl_stream(vid_url, fp):
        return {
            "ok": True, "file": fp,
            "title":    data.get("title", "TikTok"),
            "uploader": data.get("author", {}).get("nickname", "?"),
            "duration": data.get("duration", 0),
            "views":    data.get("play_count", 0),
            "likes":    data.get("digg_count", 0),
            "comments": data.get("comment_count", 0),
        }
    return None

def _src_snaptik(url, ts):
    try:
        sess = requests.Session()
        sess.headers.update({"User-Agent": rand_ua(), "Referer": "https://snaptik.app/"})
        r0  = sess.get("https://snaptik.app/vn", timeout=10)
        tok = re.search(r'name="token"\s+value="([^"]+)"', r0.text)
        if not tok: return None
        r1 = sess.post("https://snaptik.app/abc2.php",
            data={"url": url, "token": tok.group(1)}, timeout=20)
        links = re.findall(r'href="(https://[^"]+)"[^>]*class="[^"]*download', r1.text)
        if not links:
            links = re.findall(r'href="(https://[^"]+\.mp4[^"]*)"', r1.text)
        fp2 = os.path.join(DOWNLOAD_DIR, f"tiktok_{ts}_snaptik.mp4")
        for link in links[:3]:
            if dl_stream(link, fp2):
                return {"ok": True, "file": fp2,
                        "title": "TikTok Video", "uploader": "TikTok",
                        "duration": 0, "views": 0, "likes": 0, "comments": 0}
    except: pass
    return None

def _src_ssstik(url, ts):
    try:
        sess2 = requests.Session()
        sess2.headers.update({"User-Agent": rand_ua()})
        r0b = sess2.get("https://ssstik.io/en", timeout=10)
        tt  = re.search(r'tt:\s*"([^"]+)"', r0b.text)
        if not tt: return None
        r1b = sess2.post("https://ssstik.io/abc?url=dl",
            data={"id": url, "locale": "en", "tt": tt.group(1)},
            headers={"Referer": "https://ssstik.io/en",
                     "X-Requested-With": "XMLHttpRequest"}, timeout=20)
        m = re.search(r'href="(https://[^"]+)"[^>]*>.*?Without watermark',
                      r1b.text, re.S)
        if not m:
            m = re.search(r'href="(https://[^"]+\.mp4[^"]*)"', r1b.text)
        if m:
            fp3 = os.path.join(DOWNLOAD_DIR, f"tiktok_{ts}_ssstik.mp4")
            if dl_stream(m.group(1), fp3):
                return {"ok": True, "file": fp3, "title": "TikTok Video",
                        "uploader": "TikTok", "duration": 0,
                        "views": 0, "likes": 0, "comments": 0}
    except: pass
    return None

def _src_tikmate(url, ts):
    try:
        sess4 = requests.Session()
        sess4.headers.update({"User-Agent": rand_ua(), "Referer": "https://tikmate.online/"})
        r4 = sess4.post("https://tikmate.online/api/lookup",
            data={"url": url}, timeout=15)
        d4 = r4.json()
        token = d4.get("token", ""); vid_id = d4.get("id", "")
        if token and vid_id:
            dl_url = f"https://tikmate.online/download/{token}/{vid_id}.mp4?hd=1"
            fp4 = os.path.join(DOWNLOAD_DIR, f"tiktok_{ts}_tikmate.mp4")
            if dl_stream(dl_url, fp4):
                return {"ok": True, "file": fp4,
                        "title": d4.get("desc", "TikTok Video")[:60],
                        "uploader": d4.get("author_name", "TikTok"),
                        "duration": d4.get("duration", 0),
                        "views": 0, "likes": 0, "comments": 0}
    except: pass
    return None

def _src_savetik(url, ts):
    try:
        sess5 = requests.Session()
        sess5.headers.update({"User-Agent": rand_ua(), "Referer": "https://savetik.net/"})
        r5a = sess5.get("https://savetik.net/", timeout=10)
        tok5 = re.search(r'name="_token"\s+value="([^"]+)"', r5a.text)
        if not tok5: return None
        r5b = sess5.post("https://savetik.net/api/ajaxSearch",
            data={"q": url, "_token": tok5.group(1)},
            headers={"X-Requested-With": "XMLHttpRequest"}, timeout=20)
        d5 = r5b.json()
        html5 = d5.get("data", "")
        links5 = re.findall(r'href="(https://[^"]+)"[^>]*>\s*(?:HD|No watermark|Không logo)', html5, re.I)
        if not links5:
            links5 = re.findall(r'href="(https://[^"]+\.mp4[^"]*)"', html5)
        fp5 = os.path.join(DOWNLOAD_DIR, f"tiktok_{ts}_savetik.mp4")
        for lnk in links5[:3]:
            if dl_stream(lnk, fp5):
                return {"ok": True, "file": fp5,
                        "title": "TikTok Video", "uploader": "TikTok",
                        "duration": 0, "views": 0, "likes": 0, "comments": 0}
    except: pass
    return None

def _src_looptok(url, ts):
    try:
        r6 = requests.get(
            f"https://www.looptok.com/api/download?url={quote(url)}",
            headers={"User-Agent": rand_ua()}, timeout=15)
        d6 = r6.json()
        vid6 = d6.get("nowm") or d6.get("video") or d6.get("url", "")
        if vid6:
            fp6 = os.path.join(DOWNLOAD_DIR, f"tiktok_{ts}_looptok.mp4")
            if dl_stream(vid6, fp6):
                return {"ok": True, "file": fp6,
                        "title": d6.get("title", "TikTok Video")[:60],
                        "uploader": d6.get("author", "TikTok"),
                        "duration": 0, "views": 0, "likes": 0, "comments": 0}
    except: pass
    return None

def _src_tiklydown(url, ts):
    """API JSON — trả sẵn link không logo."""
    try:
        r = requests.get("https://api.tiklydown.eu.org/api/download",
            params={"url": url}, headers={"User-Agent": rand_ua()}, timeout=15)
        d = r.json()
        video = d.get("video") or {}
        vid_url = (video.get("noWatermark") or video.get("play")
                   or d.get("video_hd") or video.get("watermark"))
        if vid_url:
            fp = os.path.join(DOWNLOAD_DIR, f"tiktok_{ts}_tiklydown.mp4")
            if dl_stream(vid_url, fp):
                author = d.get("author") or {}
                return {"ok": True, "file": fp,
                        "title": str(d.get("title", "TikTok Video"))[:60],
                        "uploader": author.get("nickname", "TikTok"),
                        "duration": 0, "views": 0, "likes": 0, "comments": 0}
    except: pass
    return None

def _src_musicaldown(url, ts):
    try:
        sess = requests.Session()
        sess.headers.update({"User-Agent": rand_ua(), "Referer": "https://musicaldown.com/"})
        r0 = sess.get("https://musicaldown.com/en", timeout=10)
        tok = re.search(r'name="token"\s+value="([^"]+)"', r0.text)
        data = {"link": url}
        if tok: data["token"] = tok.group(1)
        r1 = sess.post("https://musicaldown.com/download", data=data, timeout=20)
        links = re.findall(r'href="(https://[^"]+)"[^>]*>\s*(?:Download|Tải)[^<]*(?:MP4|HD|No\s*Watermark)', r1.text, re.I)
        if not links:
            links = re.findall(r'href="(https://[^"]+\.mp4[^"]*)"', r1.text)
        fp = os.path.join(DOWNLOAD_DIR, f"tiktok_{ts}_musicaldown.mp4")
        for link in links[:3]:
            if dl_stream(link, fp):
                return {"ok": True, "file": fp, "title": "TikTok Video",
                        "uploader": "TikTok", "duration": 0,
                        "views": 0, "likes": 0, "comments": 0}
    except: pass
    return None

def _src_lovetik(url, ts):
    try:
        sess = requests.Session()
        sess.headers.update({"User-Agent": rand_ua(), "Referer": "https://lovetik.com/"})
        r1 = sess.post("https://lovetik.com/api/ajaxSearch",
            data={"query": url}, headers={"X-Requested-With": "XMLHttpRequest"}, timeout=20)
        d1 = r1.json()
        html = d1.get("data", "") if isinstance(d1, dict) else ""
        links = re.findall(r'href="(https://[^"]+)"[^>]*>\s*(?:Download\s*MP4|No\s*Watermark)', html, re.I)
        if not links:
            links = re.findall(r'href="(https://[^"]+\.mp4[^"]*)"', html)
        fp = os.path.join(DOWNLOAD_DIR, f"tiktok_{ts}_lovetik.mp4")
        for link in links[:3]:
            if dl_stream(link, fp):
                return {"ok": True, "file": fp, "title": "TikTok Video",
                        "uploader": "TikTok", "duration": 0,
                        "views": 0, "likes": 0, "comments": 0}
    except: pass
    return None

def _src_ytdlp(url, ts):
    try:
        out7 = os.path.join(DOWNLOAD_DIR, f"tiktok_ytdlp_{ts}.%(ext)s")
        opts7 = {
            "outtmpl": out7,
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "quiet": True, "no_warnings": True,
            "socket_timeout": 30,
            "merge_output_format": "mp4",
            "http_headers": TIKTOK_HEADERS_R(),
        }
        with yt_dlp.YoutubeDL(opts7) as ydl:
            info7 = ydl.extract_info(url, download=True)
            fn7   = ydl.prepare_filename(info7).replace(".webm", ".mp4").replace(".mkv", ".mp4")
            if not os.path.exists(fn7):
                for ext in ["mp4", "webm", "mkv", "m4v"]:
                    alt = fn7.rsplit(".", 1)[0] + f".{ext}"
                    if os.path.exists(alt): fn7 = alt; break
        if os.path.exists(fn7) and os.path.getsize(fn7) > 10000:
            return {
                "ok": True, "file": fn7,
                "title":    info7.get("title", "TikTok")[:60],
                "uploader": info7.get("uploader") or info7.get("creator") or "TikTok",
                "duration": info7.get("duration", 0),
                "views":    info7.get("view_count", 0),
                "likes":    info7.get("like_count", 0),
                "comments": info7.get("comment_count", 0),
            }
    except: pass
    return None

# Danh sách nguồn — muốn thêm nguồn mới chỉ cần viết thêm 1 hàm _src_xxx(url, ts)
# ở trên rồi thêm vào list này, không cần sửa gì khác.
DOWNLOAD_SOURCES = [
    _src_tikwm, _src_snaptik, _src_ssstik, _src_tikmate,
    _src_savetik, _src_looptok, _src_tiklydown, _src_musicaldown,
    _src_lovetik,
]
# yt-dlp luôn để cuối cùng vì chậm hơn các nguồn kia, dùng làm lưới an toàn cuối
FALLBACK_LAST = _src_ytdlp

# ══════════════════════════════════════════
#   TẢI VIDEO KHÔNG LOGO
# ══════════════════════════════════════════
def download_tiktok(url):
    url = resolve_url(url)
    ts  = int(time.time())

    # Xáo trộn thứ tự các nguồn (trừ yt-dlp) mỗi lần gọi — tránh việc
    # luôn luôn đập cùng 1 nguồn đầu tiên khiến nguồn đó dễ bị chặn hơn,
    # đồng thời tự động "né" nguồn nào đang chết mà không cần sửa code.
    sources = DOWNLOAD_SOURCES.copy()
    random.shuffle(sources)
    sources.append(FALLBACK_LAST)

    tried = 0
    for src_fn in sources:
        tried += 1
        try:
            result = src_fn(url, ts)
        except Exception:
            result = None
        if result:
            return result

    return {"ok": False,
            "error": f"❌ Đã thử {tried} nguồn nhưng đều bị chặn!\nThử lại sau ít phút hoặc bật VPN rồi thử."}

# ══════════════════════════════════════════
#   TẢI VIDEO HD
# ══════════════════════════════════════════
def download_tiktok_hd(url):
    """Ưu tiên hdplay → play_addr → bình thường"""
    url  = resolve_url(url)
    ts   = int(time.time())
    fp   = os.path.join(DOWNLOAD_DIR, f"tiktok_hd_{ts}.mp4")

    data = tikwm_get(url)
    if data:
        # Thử lần lượt: hdplay > play_addr > wmplay > play
        for key in ["hdplay", "play_addr", "wmplay", "play"]:
            vid_url = data.get(key)
            if vid_url and dl_stream(vid_url, fp):
                return {
                    "ok": True, "file": fp,
                    "title":    data.get("title", "TikTok"),
                    "uploader": data.get("author", {}).get("nickname", "?"),
                    "duration": data.get("duration", 0),
                    "views":    data.get("play_count", 0),
                    "likes":    data.get("digg_count", 0),
                    "comments": data.get("comment_count", 0),
                    "quality":  key,
                }
    # Fallback về download thường (đã có toàn bộ chuỗi nguồn + xáo trộn)
    return download_tiktok(url)

# ══════════════════════════════════════════
#   XEM THÔNG TIN VIDEO (KHÔNG TẢI)
# ══════════════════════════════════════════
def get_video_info(url):
    url  = resolve_url(url)
    data = tikwm_get(url)
    if data:
        author = data.get("author", {})
        return {
            "ok":       True,
            "title":    data.get("title", "TikTok Video"),
            "uploader": author.get("nickname", "?"),
            "username": author.get("unique_id", "?"),
            "duration": data.get("duration", 0),
            "views":    data.get("play_count", 0),
            "likes":    data.get("digg_count", 0),
            "comments": data.get("comment_count", 0),
            "shares":   data.get("share_count", 0),
            "created":  data.get("create_time", 0),
            "url":      url,
        }
    return {"ok": False, "error": "Không lấy được thông tin video!"}

# ══════════════════════════════════════════
#   TẢI SLIDESHOW (ẢNH + NHẠC)
# ══════════════════════════════════════════
def download_slideshow(url):
    url  = resolve_url(url)
    data = tikwm_get(url)
    if not data:
        return {"ok": False, "error": "Không lấy được dữ liệu!"}

    images = data.get("images") or data.get("image_post_info", {}).get("images", [])
    if not images:
        return {"ok": False, "error": "Video này không phải slideshow (không có ảnh)!"}

    ts        = int(time.time())
    img_files = []

    for i, img in enumerate(images[:20]):
        img_url = ""
        if isinstance(img, str):
            img_url = img
        elif isinstance(img, dict):
            img_url = (img.get("display_image", {}).get("url_list", [""])[0]
                       or img.get("url_list", [""])[0]
                       or img.get("url", ""))
        if not img_url:
            continue
        fp = os.path.join(DOWNLOAD_DIR, f"slide_{ts}_{i}.jpg")
        try:
            r = requests.get(img_url, headers=TIKTOK_HEADERS, timeout=30)
            r.raise_for_status()
            with open(fp, "wb") as f:
                f.write(r.content)
            if os.path.getsize(fp) > 1000:
                img_files.append(fp)
        except: pass

    # Lấy nhạc nền
    music_file = None
    music = data.get("music_info", {})
    music_url = music.get("play", "")
    if music_url:
        mp3 = os.path.join(DOWNLOAD_DIR, f"slide_music_{ts}.mp3")
        if dl_stream(music_url, mp3):
            music_file = mp3

    if not img_files:
        return {"ok": False, "error": "Không tải được ảnh nào!"}

    return {
        "ok":         True,
        "images":     img_files,
        "music":      music_file,
        "title":      data.get("title", "TikTok Slideshow"),
        "uploader":   data.get("author", {}).get("nickname", "?"),
        "music_title": music.get("title", ""),
        "music_author": music.get("author", ""),
    }

# ══════════════════════════════════════════
#   CẮT VIDEO (cần ffmpeg)
# ══════════════════════════════════════════
def cut_video(url, start, end):
    """Tải video rồi cắt đoạn start→end (dạng mm:ss hoặc giây)"""
    res = download_tiktok(url)
    if not res["ok"]:
        return res
    src = res["file"]
    ts  = int(time.time())
    out = os.path.join(DOWNLOAD_DIR, f"cut_{ts}.mp4")
    try:
        cmd = [
            "ffmpeg", "-y",
            "-i", src,
            "-ss", str(start),
            "-to", str(end),
            "-c", "copy",
            out
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        if result.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 1000:
            try: os.remove(src)
            except: pass
            res["file"]  = out
            res["title"] = f"[Cắt {start}→{end}] " + res["title"]
            return res
        else:
            err = result.stderr.decode("utf-8", errors="ignore")[-300:]
            try: os.remove(src)
            except: pass
            return {"ok": False, "error": f"ffmpeg lỗi:\n{err}"}
    except FileNotFoundError:
        try: os.remove(src)
        except: pass
        return {"ok": False, "error": "ffmpeg chưa được cài!\nTermux: `pkg install ffmpeg`"}
    except Exception as e:
        try: os.remove(src)
        except: pass
        return {"ok": False, "error": str(e)}

# ══════════════════════════════════════════
#   TRÍCH XUẤT PHỤ ĐỀ / CAPTION
# ══════════════════════════════════════════
def get_caption_text(url):
    """Lấy caption (mô tả) và subtitle nếu có từ tikwm"""
    url  = resolve_url(url)
    data = tikwm_get(url)
    if not data:
        return {"ok": False, "error": "Không lấy được dữ liệu video!"}

    caption = data.get("title", "")

    # Thử lấy subtitle qua yt-dlp nếu có
    subtitles_text = ""
    try:
        opts = {
            "quiet": True, "no_warnings": True,
            "writesubtitles": True, "writeautomaticsub": True,
            "subtitleslangs": ["vi", "en", "all"],
            "skip_download": True,
            "outtmpl": os.path.join(DOWNLOAD_DIR, f"sub_{int(time.time())}"),
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            subs = info.get("subtitles", {}) or info.get("automatic_captions", {})
            if subs:
                subtitles_text = "📝 *Phụ đề có sẵn:* " + ", ".join(subs.keys())
    except: pass

    return {
        "ok":        True,
        "caption":   caption,
        "subtitles": subtitles_text,
        "uploader":  data.get("author", {}).get("nickname", "?"),
        "views":     data.get("play_count", 0),
    }

# ══════════════════════════════════════════
#   THEO DÕI TIKTOKER (Watch)
# ══════════════════════════════════════════
def get_latest_video_id(username):
    """Lấy video_id mới nhất của TikToker"""
    try:
        r = requests.get(
            f"https://www.tikwm.com/api/user/posts?unique_id={username}&count=1&cursor=0",
            headers=TIKWM_HEADERS, timeout=15)
        d = r.json()
        if d.get("code") == 0:
            videos = d.get("data", {}).get("videos", [])
            if videos:
                return str(videos[0].get("video_id") or videos[0].get("id", ""))
    except: pass
    return ""

def watch_loop():
    """Chạy nền, định kỳ kiểm tra video mới của TikToker đang theo dõi"""
    while True:
        try:
            watches = db_get_all_watches()
            for watch_id, user_id, chat_id, username, last_vid_id in watches:
                try:
                    latest = get_latest_video_id(username)
                    if not latest:
                        continue
                    if latest != last_vid_id:
                        db_update_last_vid(watch_id, latest)
                        if last_vid_id:  # Không thông báo lần đầu
                            vid_url = f"https://www.tiktok.com/@{username}/video/{latest}"
                            bot.send_message(chat_id,
                                f"🔔 *@{esc(username)}* vừa đăng video mới!\\n"
                                f"🎬 [Xem / tải tại đây]({vid_url})\\n\\n"
                                f"_Dán link trên vào chat để tải không logo_",
                                parse_mode="Markdown",
                                disable_web_page_preview=True)
                    time.sleep(2)
                except: pass
        except: pass
        time.sleep(WATCH_INTERVAL)

# ══════════════════════════════════════════
#   GỬI VIDEO / ẢNH
# ══════════════════════════════════════════
def send_video(chat_id, res, del_msg_id=None):
    if not res["ok"]:
        err = esc(res.get("error", "")[:200])
        bot.send_message(chat_id, f"❌ *Tải thất bại!*\n`{err}`",
                         parse_mode="Markdown")
        return
    fp = res["file"]
    if not os.path.exists(fp):
        bot.send_message(chat_id, "❌ File không tồn tại sau khi tải"); return
    mb = os.path.getsize(fp) / 1024 / 1024
    if mb > MAX_FILE_MB:
        bot.send_message(chat_id,
            f"⚠️ File quá lớn ({mb:.1f}MB). Telegram giới hạn {MAX_FILE_MB}MB")
        try: os.remove(fp)
        except: pass
        return
    quality_tag = f"\n🎯 Chất lượng: `{res.get('quality','auto')}`" if res.get("quality") else ""
    cap = (
        f"🎬 *{esc(str(res['title'])[:60])}*\n"
        f"👤 {esc(str(res['uploader']))}\n"
        f"👁 {fmt(res['views'])}  ❤️ {fmt(res['likes'])}  "
        f"💬 {fmt(res['comments'])}\n"
        f"📦 {mb:.1f}MB  ⏱ {res['duration']}s{quality_tag}"
    )
    try:
        with open(fp, "rb") as v:
            bot.send_video(chat_id, v, caption=cap,
                           parse_mode="Markdown", supports_streaming=True)
        if del_msg_id:
            try: bot.delete_message(chat_id, del_msg_id)
            except: pass
    except Exception as e:
        bot.send_message(chat_id, f"❌ Lỗi gửi: {e}")
    finally:
        try: os.remove(fp)
        except: pass

def do_download(message, url, del_msg_id=None):
    bot.send_chat_action(message.chat.id, "upload_video")
    res = download_tiktok(url)
    send_video(message.chat.id, res, del_msg_id)
    if res["ok"]:
        db_add_history(message.from_user.id, url, res.get("title", ""))

def do_download_direct(message, best, del_msg_id=None):
    bot.send_chat_action(message.chat.id, "upload_video")
    ts = int(time.time())
    fp = os.path.join(DOWNLOAD_DIR, f"tiktok_{ts}.mp4")
    if dl_stream(best["direct"], fp):
        res = {
            "ok": True, "file": fp,
            "title":    best.get("title", "TikTok"),
            "uploader": best.get("uploader", "?"),
            "duration": 0,
            "views":    best.get("views", 0),
            "likes":    best.get("likes", 0),
            "comments": 0,
        }
    else:
        res = download_tiktok(best["url"])
    send_video(message.chat.id, res, del_msg_id)

def do_download_yt(message, url, del_msg_id=None):
    bot.send_chat_action(message.chat.id, "upload_video")
    ts  = int(time.time())
    out = os.path.join(DOWNLOAD_DIR, f"yt_{ts}.%(ext)s")
    opts = {
        "outtmpl": out,
        "format": "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "quiet": True, "no_warnings": True,
        "socket_timeout": 30,
        "merge_output_format": "mp4",
        "extractor_args": {"youtube": {"skip": ["dash", "hls"]}},
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            fn   = ydl.prepare_filename(info).replace(".webm", ".mp4").replace(".mkv", ".mp4")
            if not os.path.exists(fn):
                for ext in ["mp4", "webm", "mkv"]:
                    alt = fn.rsplit(".", 1)[0] + f".{ext}"
                    if os.path.exists(alt): fn = alt; break
        res = {
            "ok": True, "file": fn,
            "title":    info.get("title", "Video")[:60],
            "uploader": info.get("uploader") or info.get("channel") or "?",
            "duration": info.get("duration", 0),
            "views":    info.get("view_count", 0),
            "likes":    info.get("like_count", 0),
            "comments": info.get("comment_count", 0),
        }
    except Exception as e:
        res = {"ok": False, "error": str(e)[:200]}
    send_video(message.chat.id, res, del_msg_id)

# ══════════════════════════════════════════
#   TRA CỨU USER
# ══════════════════════════════════════════
def get_user_info(username):
    username = username.lstrip("@").strip()

    # PP1: tikwm
    try:
        r = requests.get(
            f"https://www.tikwm.com/api/user/info?unique_id={username}",
            headers=TIKWM_HEADERS, timeout=15)
        d = r.json()
        if d.get("code") == 0 and d.get("data"):
            u = d["data"].get("user", {})
            s = d["data"].get("stats", {})
            if u.get("nickname") or int(s.get("followerCount", 0)) > 0:
                return {
                    "ok": True,
                    "username":  u.get("uniqueId", username),
                    "nickname":  u.get("nickname", username),
                    "bio":       (u.get("signature") or "Chưa có bio")[:200],
                    "verified":  u.get("verified", False),
                    "private":   u.get("secret", False),
                    "followers": fmt(s.get("followerCount", 0)),
                    "following": fmt(s.get("followingCount", 0)),
                    "likes":     fmt(s.get("heartCount", 0)),
                    "videos":    fmt(s.get("videoCount", 0)),
                    "profile":   f"https://www.tiktok.com/@{username}",
                }
    except: pass

    # PP2: scrape tiktok.com
    try:
        mh = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) "
                          "AppleWebKit/537.36 Chrome/124 Mobile Safari/537.36",
            "Accept-Language": "vi-VN,vi;q=0.9",
        }
        r2   = requests.get(f"https://www.tiktok.com/@{username}",
                            headers=mh, timeout=20)
        html = r2.text
        def gv(k):
            m = re.search(rf'"{k}"\s*:\s*(\d+)', html)
            return int(m.group(1)) if m else 0
        def gs(k):
            m = re.search(rf'"{k}"\s*:\s*"([^"]*)"', html)
            return m.group(1) if m else ""
        nn = gs("nickname") or gs("authorName")
        fc = gv("followerCount")
        if nn or fc > 0:
            return {
                "ok": True,
                "username":  username,
                "nickname":  nn or username,
                "bio":       (gs("signature") or "Chưa có bio")[:200],
                "verified":  '"verified":true' in html,
                "private":   '"privateAccount":true' in html,
                "followers": fmt(fc),
                "following": fmt(gv("followingCount")),
                "likes":     fmt(gv("heartCount") or gv("diggCount")),
                "videos":    fmt(gv("videoCount")),
                "profile":   f"https://www.tiktok.com/@{username}",
                "note":      "⚠️ Số liệu có thể chưa chính xác",
            }
    except: pass

    return {"ok": False,
            "error": f"❌ Không tìm thấy tài khoản *@{esc(username)}*\n"
                     "Kiểm tra lại username hoặc thử sau!"}

# ══════════════════════════════════════════
#   HANDLERS
# ══════════════════════════════════════════
@bot.message_handler(commands=["start"])
def h_start(m):
    name = m.from_user.first_name or "bạn"
    bot.send_message(m.chat.id,
        f"👋 Xin chào *{esc(name)}*!\n\n"
        "🎵 *TikTok Telegram Bot v6*\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📌 /help - Xem tất cả lệnh\n"
        "💡 _Dán link TikTok → bot tự tải không logo!_",
        parse_mode="Markdown")

@bot.message_handler(commands=["help"])
def h_help(m):
    bot.send_message(m.chat.id,
        "📖 *DANH SÁCH LỆNH v6*\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📥 *TẢI VIDEO*\n"
        "🚫 `/taivd <url>` - Tải không logo\n"
        "🎯 `/hd <url>` - Tải chất lượng cao nhất\n"
        "🖼 `/slide <url>` - Tải slideshow ảnh + nhạc\n"
        "✂️ `/cut <url> <start> <end>` - Cắt đoạn video\n\n"
        "🎵 *AUDIO*\n"
        "🎵 `/music <url>` - Lấy nhạc\n\n"
        "🔍 *THÔNG TIN*\n"
        "📋 `/vinfo <url>` - Thông tin video\n"
        "📝 `/caption <url>` - Xem caption/phụ đề\n"
        "👤 `/info @user` - Tra cứu tài khoản\n\n"
        "🔔 *THEO DÕI*\n"
        "👁 `/watch @user` - Theo dõi TikToker\n"
        "🔕 `/unwatch @user` - Bỏ theo dõi\n"
        "📋 `/watchlist` - Danh sách đang theo dõi\n\n"
        "📦 *KHÁC*\n"
        "📦 `/batch` → `/done` - Tải hàng loạt\n"
        "🕐 `/history` - Lịch sử đã tải\n"
        "📊 `/stats` - Thống kê\n\n"
        "💡 _Dán link TikTok → bot tự tải không logo!_",
        parse_mode="Markdown")

# ─── TẢI VIDEO THƯỜNG ───
@bot.message_handler(commands=["taivd", "nowatermark", "nw", "nologo"])
def h_taivd(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id,
            "❌ Dùng: `/taivd <url_tiktok>`", parse_mode="Markdown")
        return
    msg = bot.send_message(message.chat.id, "⏳ Đang tải video không logo...")
    threading.Thread(target=do_download,
        args=(message, parts[1].strip(), msg.message_id)).start()

# ─── TẢI VIDEO HD ───
@bot.message_handler(commands=["hd"])
def h_hd(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id,
            "❌ Dùng: `/hd <url_tiktok>`", parse_mode="Markdown")
        return
    url = parts[1].strip()
    msg = bot.send_message(message.chat.id, "⏳ Đang tải video HD...")

    def go():
        bot.send_chat_action(message.chat.id, "upload_video")
        res = download_tiktok_hd(url)
        send_video(message.chat.id, res, msg.message_id)
        if res["ok"]:
            db_add_history(message.from_user.id, url, res.get("title", ""))

    threading.Thread(target=go).start()

# ─── TẢI SLIDESHOW ───
@bot.message_handler(commands=["slide"])
def h_slide(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id,
            "❌ Dùng: `/slide <url_tiktok>`", parse_mode="Markdown")
        return
    url = parts[1].strip()
    msg = bot.send_message(message.chat.id, "🖼 Đang tải slideshow...")

    def go():
        res = download_slideshow(url)
        if not res["ok"]:
            try: bot.edit_message_text(f"❌ {esc(res['error'])}",
                    message.chat.id, msg.message_id, parse_mode="Markdown")
            except: pass
            return

        images = res["images"]
        try: bot.delete_message(message.chat.id, msg.message_id)
        except: pass

        # Gửi ảnh theo nhóm (album)
        media_group = []
        for i, fp in enumerate(images):
            with open(fp, "rb") as f:
                media_group.append(
                    telebot.types.InputMediaPhoto(f.read(),
                        caption=f"🖼 *{esc(res['title'][:60])}*\n👤 {esc(res['uploader'])}"
                                f"\n📸 {len(images)} ảnh"
                        if i == 0 else "",
                        parse_mode="Markdown" if i == 0 else None))

        # Gửi tối đa 10 ảnh mỗi nhóm
        for i in range(0, len(media_group), 10):
            try:
                bot.send_media_group(message.chat.id, media_group[i:i+10])
            except: pass
            time.sleep(1)

        # Gửi nhạc nền nếu có
        if res.get("music"):
            try:
                with open(res["music"], "rb") as f:
                    bot.send_audio(message.chat.id, f,
                        title=res.get("music_title", "TikTok Music")[:60],
                        performer=res.get("music_author", "TikTok")[:60],
                        caption="🎵 Nhạc nền slideshow")
            except: pass
            finally:
                try: os.remove(res["music"])
                except: pass

        # Xóa ảnh tạm
        for fp in images:
            try: os.remove(fp)
            except: pass

        db_add_history(message.from_user.id, url, res.get("title", "Slideshow"))

    threading.Thread(target=go).start()

# ─── THÔNG TIN VIDEO ───
@bot.message_handler(commands=["vinfo"])
def h_vinfo(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id,
            "❌ Dùng: `/vinfo <url_tiktok>`", parse_mode="Markdown")
        return
    url = parts[1].strip()
    msg = bot.send_message(message.chat.id, "🔍 Đang lấy thông tin video...")

    def go():
        r = get_video_info(url)
        if not r["ok"]:
            try: bot.edit_message_text(f"❌ {esc(r['error'])}",
                    message.chat.id, msg.message_id, parse_mode="Markdown")
            except: pass
            return

        created = ""
        if r.get("created"):
            try:
                created = datetime.fromtimestamp(r["created"]).strftime("%d/%m/%Y")
            except: pass

        text = (
            f"📋 *THÔNG TIN VIDEO*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📝 *Caption:* {esc(str(r['title'])[:120])}\n\n"
            f"👤 *Tác giả:* {esc(r['uploader'])} (@{esc(r['username'])})\n"
            f"⏱ *Thời lượng:* {r['duration']}s\n"
            f"📅 *Ngày đăng:* {created}\n\n"
            f"👁 *Lượt xem:* {fmt(r['views'])}\n"
            f"❤️ *Likes:* {fmt(r['likes'])}\n"
            f"💬 *Comments:* {fmt(r['comments'])}\n"
            f"↗️ *Shares:* {fmt(r['shares'])}\n\n"
            f"🔗 [Xem trên TikTok]({r['url']})"
        )
        try: bot.edit_message_text(text, message.chat.id, msg.message_id,
                parse_mode="Markdown", disable_web_page_preview=True)
        except: bot.send_message(message.chat.id, text,
                parse_mode="Markdown", disable_web_page_preview=True)

    threading.Thread(target=go).start()

# ─── CẮT VIDEO ───
@bot.message_handler(commands=["cut"])
def h_cut(message):
    """Dùng: /cut <url> <start> <end>  (ví dụ: /cut https://... 0:10 0:30)"""
    parts = message.text.split()
    if len(parts) < 4:
        bot.send_message(message.chat.id,
            "❌ Dùng: `/cut <url> <start> <end>`\n"
            "Ví dụ: `/cut https://tiktok.com/... 0:10 0:30`\n"
            "Hoặc:  `/cut https://tiktok.com/... 10 30` (đơn vị giây)",
            parse_mode="Markdown")
        return

    url   = parts[1].strip()
    start = parts[2].strip()
    end   = parts[3].strip()

    if not has_ffmpeg():
        bot.send_message(message.chat.id,
            "⚠️ *ffmpeg chưa được cài!*\n"
            "Cài trên Termux: `pkg install ffmpeg`\n"
            "Sau đó thử lại lệnh này.",
            parse_mode="Markdown")
        return

    msg = bot.send_message(message.chat.id,
        f"✂️ Đang tải & cắt `{start}` → `{end}`...",
        parse_mode="Markdown")

    def go():
        bot.send_chat_action(message.chat.id, "upload_video")
        res = cut_video(url, start, end)
        send_video(message.chat.id, res, msg.message_id)

    threading.Thread(target=go).start()

# ─── CAPTION / PHỤ ĐỀ ───
@bot.message_handler(commands=["caption"])
def h_caption(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id,
            "❌ Dùng: `/caption <url_tiktok>`", parse_mode="Markdown")
        return
    url = parts[1].strip()
    msg = bot.send_message(message.chat.id, "📝 Đang lấy caption...")

    def go():
        r = get_caption_text(url)
        if not r["ok"]:
            try: bot.edit_message_text(f"❌ {esc(r['error'])}",
                    message.chat.id, msg.message_id, parse_mode="Markdown")
            except: pass
            return

        text = (
            f"📝 *CAPTION & PHỤ ĐỀ*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 *{esc(r['uploader'])}*  👁 {fmt(r['views'])}\n\n"
            f"📄 *Caption:*\n{esc(r['caption'][:800])}"
        )
        if r.get("subtitles"):
            text += f"\n\n{r['subtitles']}"

        try: bot.edit_message_text(text, message.chat.id, msg.message_id,
                parse_mode="Markdown")
        except: bot.send_message(message.chat.id, text, parse_mode="Markdown")

    threading.Thread(target=go).start()

# ─── LỊCH SỬ ───
@bot.message_handler(commands=["history"])
def h_history(message):
    rows = db_get_history(message.from_user.id, limit=10)
    if not rows:
        bot.send_message(message.chat.id,
            "📭 Chưa có lịch sử tải nào.\nDán link TikTok để bắt đầu!")
        return
    text = "🕐 *LỊCH SỬ TẢI GẦN ĐÂY*\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    for i, (url, title, ts) in enumerate(rows, 1):
        dt = datetime.fromtimestamp(ts).strftime("%d/%m %H:%M")
        text += f"{i}. [{esc(str(title)[:40])}]({url})\n   _{dt}_\n\n"
    bot.send_message(message.chat.id, text,
        parse_mode="Markdown", disable_web_page_preview=True)

# ─── THEO DÕI TIKTOKER ───
@bot.message_handler(commands=["watch"])
def h_watch(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id,
            "❌ Dùng: `/watch @username`\n"
            "Ví dụ: `/watch @charlidamelio`", parse_mode="Markdown")
        return
    username = parts[1].strip().lstrip("@")
    msg = bot.send_message(message.chat.id,
        f"🔍 Đang kiểm tra tài khoản @{esc(username)}...",
        parse_mode="Markdown")

    def go():
        # Kiểm tra user tồn tại không
        r = get_user_info(username)
        if not r["ok"]:
            try: bot.edit_message_text(
                    f"❌ Không tìm thấy tài khoản *@{esc(username)}*!\n"
                    "Kiểm tra lại username.",
                    message.chat.id, msg.message_id, parse_mode="Markdown")
            except: pass
            return

        # Lấy video mới nhất hiện tại để làm mốc
        last_vid = get_latest_video_id(username)
        ok = db_add_watch(message.from_user.id, message.chat.id, username, last_vid)

        if ok:
            try: bot.edit_message_text(
                    f"✅ *Đã theo dõi @{esc(username)}!*\n\n"
                    f"👤 *{esc(r['nickname'])}*\n"
                    f"👥 {r['followers']} followers\n\n"
                    f"🔔 Bot sẽ thông báo khi có video mới.\n"
                    f"_Kiểm tra mỗi {WATCH_INTERVAL//60} phút._",
                    message.chat.id, msg.message_id, parse_mode="Markdown")
            except: pass
        else:
            try: bot.edit_message_text(
                    f"⚠️ Đã theo dõi @{esc(username)} rồi!",
                    message.chat.id, msg.message_id, parse_mode="Markdown")
            except: pass

    threading.Thread(target=go).start()

@bot.message_handler(commands=["unwatch"])
def h_unwatch(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id,
            "❌ Dùng: `/unwatch @username`", parse_mode="Markdown")
        return
    username = parts[1].strip().lstrip("@")
    ok = db_remove_watch(message.from_user.id, username)
    if ok:
        bot.send_message(message.chat.id,
            f"🔕 Đã bỏ theo dõi *@{esc(username)}*.", parse_mode="Markdown")
    else:
        bot.send_message(message.chat.id,
            f"⚠️ Không tìm thấy @{esc(username)} trong danh sách theo dõi.")

@bot.message_handler(commands=["watchlist"])
def h_watchlist(message):
    names = db_get_watchlist(message.from_user.id)
    if not names:
        bot.send_message(message.chat.id,
            "📭 Chưa theo dõi TikToker nào.\n"
            "Dùng `/watch @username` để theo dõi.", parse_mode="Markdown")
        return
    text = "👁 *ĐANG THEO DÕI*\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    for i, name in enumerate(names, 1):
        text += f"{i}. [@{esc(name)}](https://www.tiktok.com/@{name})\n"
    text += f"\n_Tổng: {len(names)} TikToker_"
    bot.send_message(message.chat.id, text,
        parse_mode="Markdown", disable_web_page_preview=True)

# ─── INFO USER ───
@bot.message_handler(commands=["info"])
def h_info(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id,
            "❌ Dùng: `/info @username`", parse_mode="Markdown")
        return
    username = parts[1].strip()
    msg = bot.send_message(message.chat.id,
        f"🔍 Đang tra cứu `{esc(username)}`...", parse_mode="Markdown")

    def go():
        r = get_user_info(username)
        if not r["ok"]:
            try: bot.edit_message_text(r["error"], message.chat.id,
                    msg.message_id, parse_mode="Markdown")
            except: bot.send_message(message.chat.id, r["error"],
                    parse_mode="Markdown")
            return
        verified = " ✅" if r.get("verified") else ""
        lock     = "🔒 Riêng tư" if r.get("private") else "🌐 Công khai"
        note     = f"\n\n_{esc(r['note'])}_" if r.get("note") else ""
        text = (
            f"👤 *THÔNG TIN TIKTOK*{verified}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📛 *{esc(r['nickname'])}*\n"
            f"🔗 @{esc(r['username'])}  |  {lock}\n\n"
            f"👥 *Followers:* {r['followers']}\n"
            f"➡️ *Following:* {r['following']}\n"
            f"❤️ *Tổng likes:* {r['likes']}\n"
            f"🎬 *Videos:* {r['videos']}\n\n"
            f"📝 _{esc(r['bio'])}_\n\n"
            f"🔗 [Xem hồ sơ]({r['profile']}){note}"
        )
        try: bot.edit_message_text(text, message.chat.id, msg.message_id,
                parse_mode="Markdown", disable_web_page_preview=True)
        except: bot.send_message(message.chat.id, text,
                parse_mode="Markdown", disable_web_page_preview=True)

    threading.Thread(target=go).start()

# ─── NHẠC ───
@bot.message_handler(commands=["music"])
def h_music(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id,
            "❌ Dùng: `/music <url>`", parse_mode="Markdown")
        return
    url = parts[1].strip()
    msg = bot.send_message(message.chat.id, "🎵 Đang lấy nhạc...")

    def go():
        resolved  = resolve_url(url)
        ts        = int(time.time())
        sent      = False

        data = tikwm_get(resolved)
        if data:
            music     = data.get("music_info", {})
            music_url = music.get("play", "")
            title     = str(music.get("title", "TikTok Music"))[:60]
            author    = str(music.get("author", "TikTok"))[:60]
            if music_url:
                mp3 = os.path.join(DOWNLOAD_DIR, f"audio_{ts}.mp3")
                if dl_stream(music_url, mp3):
                    try:
                        bot.send_chat_action(message.chat.id, "upload_audio")
                        with open(mp3, "rb") as f:
                            bot.send_audio(message.chat.id, f,
                                title=title, performer=author,
                                caption="🎵 TikTok Music")
                        try: bot.delete_message(message.chat.id, msg.message_id)
                        except: pass
                        sent = True
                    except: pass
                    finally:
                        try: os.remove(mp3)
                        except: pass

        if sent: return

        try:
            out  = os.path.join(DOWNLOAD_DIR, f"audio_{ts}.%(ext)s")
            opts = {"outtmpl": out, "format": "bestaudio/best",
                    "quiet": True, "no_warnings": True,
                    "http_headers": TIKTOK_HEADERS, "socket_timeout": 30}
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(resolved, download=True)
                fn   = ydl.prepare_filename(info)
            if not os.path.exists(fn):
                for ext in ["m4a", "webm", "mp4", "ogg", "aac"]:
                    alt = fn.rsplit(".", 1)[0] + f".{ext}"
                    if os.path.exists(alt): fn = alt; break
            if os.path.exists(fn):
                bot.send_chat_action(message.chat.id, "upload_audio")
                with open(fn, "rb") as f:
                    bot.send_audio(message.chat.id, f,
                        title=str(info.get("title") or "TikTok")[:60],
                        performer=str(info.get("uploader") or "TikTok")[:60],
                        caption="🎵 TikTok Audio")
                try: bot.delete_message(message.chat.id, msg.message_id)
                except: pass
                try: os.remove(fn)
                except: pass
                return
        except: pass

        try:
            bot.edit_message_text(
                "❌ Không lấy được nhạc.\nDùng `/taivd` để tải cả video.",
                message.chat.id, msg.message_id, parse_mode="Markdown")
        except: pass

    threading.Thread(target=go).start()

# ─── BATCH ───
@bot.message_handler(commands=["batch"])
def h_batch(m):
    batch_sessions[m.from_user.id] = []
    bot.send_message(m.chat.id,
        "📦 *Chế độ tải hàng loạt*\n\n"
        "Gửi từng link TikTok vào đây.\n"
        "✅ /done - Tải tất cả không logo\n"
        "❌ /cancel - Hủy", parse_mode="Markdown")

@bot.message_handler(commands=["done"])
def h_done(message):
    uid  = message.from_user.id
    urls = batch_sessions.get(uid, [])
    if not urls:
        bot.send_message(message.chat.id,
            "❌ Chưa có link!\nDùng /batch để bắt đầu.")
        return
    batch_sessions.pop(uid)
    bot.send_message(message.chat.id, f"🚀 Tải {len(urls)} video không logo...")

    def go():
        for i, url in enumerate(urls, 1):
            m2 = bot.send_message(message.chat.id,
                f"📥 [{i}/{len(urls)}] Đang tải...")
            do_download(message, url, m2.message_id)
            time.sleep(2)
        bot.send_message(message.chat.id, f"✅ Xong! Đã tải {len(urls)} video.")

    threading.Thread(target=go).start()

@bot.message_handler(commands=["cancel"])
def h_cancel(m):
    if m.from_user.id in batch_sessions:
        del batch_sessions[m.from_user.id]
        bot.send_message(m.chat.id, "✅ Đã hủy phiên tải hàng loạt.")
    else:
        bot.send_message(m.chat.id, "Không có phiên nào đang chạy.")

# ─── THỐNG KÊ ───
@bot.message_handler(commands=["stats"])
def h_stats(m):
    try:
        files = [f for f in os.listdir(DOWNLOAD_DIR)
                 if os.path.isfile(os.path.join(DOWNLOAD_DIR, f))]
        total = sum(os.path.getsize(os.path.join(DOWNLOAD_DIR, f))
                    for f in files)
    except: files, total = [], 0
    # Đếm watch và history từ DB
    try:
        con  = sqlite3.connect(DB_PATH)
        hist = con.execute("SELECT COUNT(*) FROM history WHERE user_id=?",
                           (m.from_user.id,)).fetchone()[0]
        watches = con.execute("SELECT COUNT(*) FROM watchlist WHERE user_id=?",
                              (m.from_user.id,)).fetchone()[0]
        con.close()
    except: hist, watches = 0, 0

    bot.send_message(m.chat.id,
        f"📊 *THỐNG KÊ*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📁 Cache: {len(files)} file ({total/1024/1024:.1f}MB)\n"
        f"🕐 Lịch sử của bạn: {hist} video\n"
        f"👁 Đang theo dõi: {watches} TikToker\n"
        f"⏰ {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}",
        parse_mode="Markdown")

# ─── AUTO LINK ───
@bot.message_handler(func=lambda m: m.text and any(
    x in m.text for x in ["tiktok.com", "vm.tiktok", "vt.tiktok"]))
def h_auto(message):
    uid  = message.from_user.id
    urls = re.findall(
        r"https?://(?:www\.|vm\.|vt\.|m\.)?tiktok\.com/\S+", message.text)
    if not urls: return
    if uid in batch_sessions:
        batch_sessions[uid].extend(urls)
        bot.send_message(message.chat.id,
            f"✅ Thêm {len(urls)} link "
            f"(Tổng: {len(batch_sessions[uid])})\n"
            "Gõ /done để tải tất cả")
        return
    for url in urls[:3]:
        msg = bot.send_message(message.chat.id, "⏳ Đang tải không logo...")
        threading.Thread(target=do_download,
            args=(message, url, msg.message_id)).start()

@bot.message_handler(func=lambda m: True)
def h_unknown(m):
    if m.text and m.text.startswith("/"):
        bot.send_message(m.chat.id,
            "❓ Lệnh không tồn tại! Gõ /help để xem danh sách.")

# ══════════════════════════════════════════
#   CHẠY BOT
# ══════════════════════════════════════════
if __name__ == "__main__":
    db_init()
    print("🤖 TikTok Bot v6 đang chạy...")
    print(f"📁 Lưu file: {DOWNLOAD_DIR}")
    print(f"🗄 Database: {DB_PATH}")
    print(f"ffmpeg: {'✅ Có sẵn' if has_ffmpeg() else '❌ Chưa cài (cần cho /cut)'}")

    # Thread dọn file cũ
    threading.Thread(target=lambda: [
        (time.sleep(1800), clean_old()) for _ in iter(int, 1)],
        daemon=True).start()

    # Thread theo dõi TikToker
    threading.Thread(target=watch_loop, daemon=True).start()

    while True:
        try:
            bot.infinity_polling(timeout=30, long_polling_timeout=20)
        except Exception as e:
            print(f"⚠️ Lỗi: {e} — kết nối lại sau 5s...")
            time.sleep(5)
