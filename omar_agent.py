"""
OMAR Agent — AI-powered YouTube Shorts automation

Finds trending viral videos, clips the best moments in TikTok format
with synced captions, generates titles/descriptions/hashtags, and
uploads them as YouTube Shorts to your channel.

Usage:
    python omar_agent.py              # Interactive mode
    python omar_agent.py --auto       # Full auto mode (find + clip + upload)
    python omar_agent.py --url URL    # Process a specific video
    python omar_agent.py --auth       # Set up YouTube OAuth2 (first time only)
"""

import os
import re
import sys
import json
import math
import uuid
import shutil
import random
import argparse
import subprocess
import time
from datetime import datetime

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

AGENT_NAME = "Omar"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "omar_output")
CREDS_DIR = os.path.join(BASE_DIR, "credentials")
TOKEN_FILE = os.path.join(CREDS_DIR, "token.json")
CLIENT_SECRET_FILE = os.path.join(CREDS_DIR, "client_secret.json")
HISTORY_FILE = os.path.join(BASE_DIR, "omar_history.json")

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(CREDS_DIR, exist_ok=True)

# Niche keywords for trending video discovery
VIRAL_SEARCH_QUERIES = [
    "viral moment today",
    "most shocking moments",
    "unbelievable caught on camera",
    "funniest moments this week",
    "craziest things ever seen",
    "you won't believe what happened",
    "most unexpected moments",
    "trending viral clips",
    "insane moments caught on camera",
    "best fails and wins",
    "jaw dropping moments",
    "plot twist moments",
]

# Title templates for Shorts
TITLE_TEMPLATES = [
    "{hook} #shorts",
    "{hook} (WAIT FOR IT) #shorts",
    "{hook}... #shorts",
    "This is INSANE - {hook} #shorts",
    "Nobody expected THIS - {hook} #shorts",
    "Wait till you see this... {hook} #shorts",
    "{hook} (MUST WATCH) #shorts",
]

# Hashtag pool
HASHTAGS = [
    "#shorts", "#viral", "#trending", "#fyp", "#foryou",
    "#mindblowing", "#crazy", "#unbelievable", "#omg",
    "#mustsee", "#insane", "#wow", "#amazing", "#shocking",
    "#entertainment", "#viralshorts", "#fypshorts",
]

# ---------------------------------------------------------------------------
# Tool paths (same logic as app.py)
# ---------------------------------------------------------------------------

def _find_ffmpeg():
    found = shutil.which("ffmpeg")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        winget_path = os.path.join(
            local, "Microsoft", "WinGet", "Packages",
            "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe",
            "ffmpeg-8.1-full_build", "bin", "ffmpeg.exe",
        )
        if os.path.exists(winget_path):
            return winget_path
    return "ffmpeg"

FFMPEG_PATH = _find_ffmpeg()
YTDLP_PATH = shutil.which("yt-dlp") or "yt-dlp"


# ---------------------------------------------------------------------------
# History tracking (avoid re-processing same videos)
# ---------------------------------------------------------------------------

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    return {"processed": [], "uploaded": []}


def save_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


# ---------------------------------------------------------------------------
# YouTube OAuth2 Authentication
# ---------------------------------------------------------------------------

def setup_oauth():
    """Set up OAuth2 for YouTube uploads. Run once."""
    print(f"\n  [{AGENT_NAME}] Setting up YouTube OAuth2...\n")

    if not os.path.exists(CLIENT_SECRET_FILE):
        print(f"  [{AGENT_NAME}] You need a client_secret.json file.")
        print(f"  [{AGENT_NAME}] Here's how to get one:\n")
        print("  1. Go to https://console.cloud.google.com/")
        print("  2. Select your project (or create one)")
        print("  3. Go to APIs & Services > Credentials")
        print("  4. Click 'Create Credentials' > 'OAuth Client ID'")
        print("  5. Application type: 'Desktop App'")
        print("  6. Download the JSON file")
        print(f"  7. Save it as: {CLIENT_SECRET_FILE}")
        print()
        print("  ALSO make sure you've enabled the YouTube Data API v3")
        print("  AND set up an OAuth consent screen (External, Testing)")
        print("  AND added your Google account as a test user")
        print()
        input("  Press Enter once you've saved client_secret.json...")

        if not os.path.exists(CLIENT_SECRET_FILE):
            print(f"\n  [{AGENT_NAME}] File not found. Try again.")
            return False

    from google_auth_oauthlib.flow import InstalledAppFlow
    SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
              "https://www.googleapis.com/auth/youtube"]

    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
    credentials = flow.run_local_server(port=8090)

    with open(TOKEN_FILE, "w") as f:
        f.write(credentials.to_json())

    print(f"\n  [{AGENT_NAME}] Authentication successful! Token saved.")
    return True


def get_youtube_upload_service():
    """Get authenticated YouTube service for uploads."""
    if not os.path.exists(TOKEN_FILE):
        print(f"  [{AGENT_NAME}] Not authenticated. Run: python omar_agent.py --auth")
        return None

    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
              "https://www.googleapis.com/auth/youtube"]

    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)


# ---------------------------------------------------------------------------
# Trending Video Discovery
# ---------------------------------------------------------------------------

def find_trending_videos(api_key, max_results=10):
    """Search YouTube for trending viral videos."""
    from googleapiclient.discovery import build

    print(f"  [{AGENT_NAME}] Searching for trending viral videos...")

    youtube = build("youtube", "v3", developerKey=api_key)
    history = load_history()
    candidates = []

    # Pick 3 random search queries
    queries = random.sample(VIRAL_SEARCH_QUERIES, min(3, len(VIRAL_SEARCH_QUERIES)))

    for query in queries:
        print(f"  [{AGENT_NAME}] Searching: '{query}'")
        try:
            resp = youtube.search().list(
                part="snippet",
                q=query,
                type="video",
                order="viewCount",
                publishedAfter=_recent_date(),
                videoDuration="medium",  # 4-20 min videos
                maxResults=5,
            ).execute()

            for item in resp.get("items", []):
                vid = item["id"]["videoId"]
                if vid not in history["processed"]:
                    candidates.append({
                        "video_id": vid,
                        "title": item["snippet"]["title"],
                        "channel": item["snippet"]["channelTitle"],
                        "description": item["snippet"]["description"][:200],
                    })
        except Exception as e:
            print(f"  [{AGENT_NAME}] Search error: {e}")

    # Deduplicate
    seen = set()
    unique = []
    for c in candidates:
        if c["video_id"] not in seen:
            seen.add(c["video_id"])
            unique.append(c)

    print(f"  [{AGENT_NAME}] Found {len(unique)} new candidate videos")
    return unique[:max_results]


def _recent_date():
    """Return ISO date string for 7 days ago."""
    from datetime import timedelta
    d = datetime.utcnow() - timedelta(days=7)
    return d.strftime("%Y-%m-%dT00:00:00Z")


# ---------------------------------------------------------------------------
# Transcript & Viral Analysis (reusing OMARQI logic)
# ---------------------------------------------------------------------------

from youtube_transcript_api import YouTubeTranscriptApi

VIRAL_KEYWORDS = {
    "shock": ["shocking", "unbelievable", "insane", "crazy", "mind-blowing", "wild",
              "unexpected", "plot twist", "jaw-dropping", "stunned", "speechless"],
    "secret": ["secret", "hidden", "revealed", "exposed", "truth", "nobody knows",
               "leaked", "confidential"],
    "superlative": ["best", "worst", "greatest", "most", "never before", "first ever",
                    "only", "legendary", "ultimate", "record-breaking"],
    "urgency": ["breaking", "just happened", "right now", "urgent",
                "happening now", "live", "update"],
    "emotion": ["hilarious", "heartbreaking", "terrifying", "amazing", "incredible",
                "beautiful", "devastating", "emotional", "inspiring"],
}

RHETORICAL_PATTERNS = [
    r"have you ever", r"did you know", r"can you believe", r"what if",
    r"how is this", r"why does nobody", r"who would have thought",
]

ENGAGEMENT_HOOKS = [
    "so what happened was", "you won't believe", "here's the thing",
    "let me tell you", "wait for it", "watch this", "listen to this",
    "but here's the twist", "guess what", "check this out",
    "the crazy part is", "plot twist", "but wait",
]

CLIFFHANGER_PHRASES = [
    "but then", "and suddenly", "out of nowhere", "everything changed",
    "that's when", "until", "and that's not all",
]

INTENSITY_WORDS = [
    "love", "hate", "amazing", "incredible", "beautiful", "perfect",
    "terrible", "awful", "disgusting", "horrifying", "absolutely",
    "completely", "totally", "literally", "honestly", "seriously",
]


def fetch_transcript(video_id):
    api = YouTubeTranscriptApi()
    try:
        result = api.fetch(video_id)
        return [{"text": s.text, "start": s.start, "duration": s.duration}
                for s in result.snippets], None
    except Exception as e:
        try:
            transcript_list = api.list(video_id)
            for t in transcript_list:
                try:
                    result = t.translate("en").fetch()
                    return [{"text": s.text, "start": s.start, "duration": s.duration}
                            for s in result.snippets], None
                except Exception:
                    result = t.fetch()
                    return [{"text": s.text, "start": s.start, "duration": s.duration}
                            for s in result.snippets], None
        except Exception:
            pass
        return [], str(e)


def analyze_viral_moments(transcript):
    """Find top 5 viral moments from transcript."""
    if not transcript:
        return []

    total_words = sum(len(seg["text"].split()) for seg in transcript)
    total_duration = sum(seg["duration"] for seg in transcript)
    global_mean_wps = total_words / max(total_duration, 0.001)

    windows = _build_windows(transcript)
    if not windows:
        return []

    scored = []
    for w in windows:
        s_kw = _score_keywords(w["text"])
        s_q = _score_questions(w["text"])
        s_em = _score_emotion(w["text"])
        s_pc = _score_pacing(w["segments"], global_mean_wps)
        s_hk = _score_hooks(w["text"])

        final = (s_kw * 0.25) + (s_q * 0.20) + (s_em * 0.25) + (s_pc * 0.20) + (s_hk * 0.10)

        scored.append({
            "start": round(w["start"], 2),
            "end": round(w["end"], 2),
            "duration": round(w["duration"], 2),
            "score": round(final * 100, 1),
            "excerpt": w["text"][:150],
        })

    scored.sort(key=lambda x: x["score"], reverse=True)

    # Top 5 non-overlapping
    selected = []
    for c in scored:
        if len(selected) >= 5:
            break
        overlap = any(not (c["end"] <= s["start"] + 5 or c["start"] >= s["end"] - 5) for s in selected)
        if not overlap:
            selected.append(c)

    selected.sort(key=lambda x: x["start"])
    for i, clip in enumerate(selected):
        clip["label"] = f"Clip {i + 1}"

    return selected


def _build_windows(transcript, min_dur=15, target_dur=30, max_dur=60):
    windows = []
    n = len(transcript)
    for i in range(n):
        start = transcript[i]["start"]
        texts, segs = [], []
        end = start
        for j in range(i, n):
            seg = transcript[j]
            seg_end = seg["start"] + seg["duration"]
            if seg_end - start > max_dur:
                break
            texts.append(seg["text"])
            segs.append(seg)
            end = seg_end
            if seg_end - start >= target_dur:
                break
        if end - start >= min_dur:
            windows.append({"start": start, "end": end, "duration": end - start,
                            "text": " ".join(texts), "segments": segs})
    return windows


def _score_keywords(text):
    words = text.lower().split()
    if not words: return 0.0
    count = sum(text.lower().count(kw) for cat in VIRAL_KEYWORDS.values() for kw in cat)
    return min((count / len(words)) * 100 / 10.0, 1.0)

def _score_questions(text):
    q = text.count("?")
    r = sum(len(re.findall(p, text.lower())) for p in RHETORICAL_PATTERNS)
    return min((q * 2 + r * 3) / 10.0, 1.0)

def _score_emotion(text):
    words = text.split()
    if not words: return 0.0
    ic = sum(1 for w in INTENSITY_WORDS if w in text.lower())
    ec = text.count("!")
    cc = sum(1 for w in words if w.isupper() and len(w) > 2)
    return min((ic + ec + cc) / len(words) * 100 / 10.0, 1.0)

def _score_pacing(segments, global_wps):
    if not segments or global_wps == 0: return 0.0
    local = [len(s["text"].split()) / max(s["duration"], 0.01) for s in segments]
    mean = sum(local) / len(local)
    dev = abs(mean - global_wps) / max(global_wps, 0.001)
    var = 0
    if len(local) > 1:
        var = min(math.sqrt(sum((v - mean) ** 2 for v in local) / len(local)) / 2.0, 1.0)
    return min((dev + var) / 2.0, 1.0)

def _score_hooks(text):
    tl = text.lower()
    h = sum(1 for hook in ENGAGEMENT_HOOKS if hook in tl)
    c = sum(1 for phrase in CLIFFHANGER_PHRASES if phrase in tl)
    return min((h + c) * 2 / 10.0, 1.0)


# ---------------------------------------------------------------------------
# Video Download & Clip Cutting (with TikTok format + captions)
# ---------------------------------------------------------------------------

def download_video(video_id, output_dir):
    output_path = os.path.join(output_dir, f"{video_id}.mp4")
    url = f"https://www.youtube.com/watch?v={video_id}"
    cmd = [
        YTDLP_PATH,
        "-f", "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", output_path,
        "--no-playlist", "--no-warnings",
        "--ffmpeg-location", os.path.dirname(FFMPEG_PATH),
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr[:500]}")
    if os.path.exists(output_path):
        return output_path
    for f in os.listdir(output_dir):
        if f.startswith(video_id) and f.endswith(".mp4"):
            return os.path.join(output_dir, f)
    raise FileNotFoundError("Downloaded video not found")


def format_ass_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def generate_ass_subtitles(transcript, clip_start, clip_end, ass_path):
    header = """[Script Info]
Title: OMAR Agent Captions
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,72,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,0,2,40,40,200,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for seg in transcript:
        seg_start = seg["start"]
        seg_end = seg_start + seg["duration"]
        if seg_end <= clip_start or seg_start >= clip_end:
            continue
        rel_start = max(seg_start - clip_start, 0)
        rel_end = min(seg_end - clip_start, clip_end - clip_start)
        text = seg["text"].strip()
        if not text:
            continue
        words = text.split()
        if len(words) > 5:
            chunk_dur = (rel_end - rel_start) / max(math.ceil(len(words) / 4), 1)
            for c in range(0, len(words), 4):
                chunk = " ".join(words[c:c + 4])
                c_start = rel_start + (c / max(len(words), 1)) * (rel_end - rel_start)
                c_end = min(c_start + chunk_dur, rel_end)
                events.append((c_start, c_end, chunk.upper()))
        else:
            events.append((rel_start, rel_end, text.upper()))

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(header)
        for st, et, txt in events:
            clean = txt.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
            f.write(f"Dialogue: 0,{format_ass_time(st)},{format_ass_time(et)},Default,,0,0,0,,{clean}\n")
    return ass_path


def cut_clip(input_path, start, end, output_path, transcript=None):
    duration = end - start
    ass_path = output_path.replace(".mp4", ".ass")
    has_subs = False
    if transcript:
        generate_ass_subtitles(transcript, start, end, ass_path)
        has_subs = os.path.exists(ass_path)

    if has_subs:
        escaped_ass = ass_path.replace("\\", "/").replace(":", "\\:")
        vf = f"crop=ih*9/16:ih,scale=1080:1920,setsar=1,ass='{escaped_ass}'"
    else:
        vf = "crop=ih*9/16:ih,scale=1080:1920,setsar=1"

    cmd = [
        FFMPEG_PATH, "-y", "-ss", str(start), "-i", input_path, "-t", str(duration),
        "-vf", vf, "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        "-movflags", "+faststart", "-pix_fmt", "yuv420p", output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if has_subs:
        try: os.remove(ass_path)
        except OSError: pass

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr[:500]}")
    return output_path


# ---------------------------------------------------------------------------
# Title / Description / Hashtag Generation
# ---------------------------------------------------------------------------

def generate_title(excerpt, video_title):
    """Generate a catchy Short title from the clip excerpt."""
    # Extract a hook phrase from the excerpt
    words = excerpt.split()[:10]
    hook = " ".join(words)

    # Clean up
    hook = re.sub(r'[^\w\s\'-]', '', hook).strip()
    if len(hook) > 60:
        hook = hook[:57] + "..."

    # Pick a template
    template = random.choice(TITLE_TEMPLATES)
    title = template.format(hook=hook)

    # Ensure under 100 chars (YouTube limit)
    if len(title) > 100:
        title = hook[:90] + " #shorts"

    return title


def generate_description(excerpt, video_title, source_video_id):
    """Generate a YouTube Shorts description."""
    # Pick 5-7 random hashtags
    tags = random.sample(HASHTAGS, min(7, len(HASHTAGS)))
    tag_line = " ".join(tags)

    desc = (
        f"Generated by OMAR Agent\n\n"
        f"{excerpt[:200]}\n\n"
        f"Original video: https://youtube.com/watch?v={source_video_id}\n\n"
        f"{tag_line}\n\n"
        f"Subscribe for more viral clips daily!"
    )
    return desc


def generate_tags(excerpt):
    """Generate keyword tags for the upload."""
    base_tags = ["shorts", "viral", "trending", "fyp", "entertainment",
                 "funny", "crazy", "amazing", "mustsee"]
    # Add words from excerpt
    words = re.findall(r'\b[a-zA-Z]{4,}\b', excerpt.lower())
    extra = list(set(words))[:5]
    return base_tags + extra


# ---------------------------------------------------------------------------
# YouTube Upload
# ---------------------------------------------------------------------------

def upload_to_youtube(youtube_service, video_path, title, description, tags):
    """Upload a video to YouTube as a Short."""
    from googleapiclient.http import MediaFileUpload

    print(f"  [{AGENT_NAME}] Uploading: {title}")

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "24",  # Entertainment
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)

    request = youtube_service.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            print(f"  [{AGENT_NAME}] Upload progress: {pct}%")

    video_id = response["id"]
    print(f"  [{AGENT_NAME}] Uploaded! https://youtube.com/shorts/{video_id}")
    return video_id


# ---------------------------------------------------------------------------
# Main Agent Workflow
# ---------------------------------------------------------------------------

def process_video(video_id, video_title, transcript, session_dir, youtube_service=None):
    """Process a single video: analyze, clip, and optionally upload."""
    print(f"\n  [{AGENT_NAME}] Analyzing transcript ({len(transcript)} segments)...")
    moments = analyze_viral_moments(transcript)

    if not moments:
        print(f"  [{AGENT_NAME}] No viral moments found. Skipping.")
        return []

    print(f"  [{AGENT_NAME}] Found {len(moments)} viral moments!")
    for m in moments:
        mins = int(m['start'] // 60)
        secs = int(m['start'] % 60)
        print(f"    - {m['label']}: {mins}:{secs:02d} (score: {m['score']}%)")

    # Download video
    print(f"\n  [{AGENT_NAME}] Downloading video...")
    try:
        video_path = download_video(video_id, session_dir)
    except Exception as e:
        print(f"  [{AGENT_NAME}] Download failed: {e}")
        return []

    # Cut clips
    uploaded = []
    for i, moment in enumerate(moments):
        clip_name = f"short_{i + 1}.mp4"
        clip_path = os.path.join(session_dir, clip_name)

        print(f"\n  [{AGENT_NAME}] Cutting {moment['label']}...")
        try:
            cut_clip(video_path, moment["start"], moment["end"], clip_path, transcript=transcript)
        except Exception as e:
            print(f"  [{AGENT_NAME}] Cut failed: {e}")
            continue

        # Generate metadata
        title = generate_title(moment["excerpt"], video_title)
        description = generate_description(moment["excerpt"], video_title, video_id)
        tags = generate_tags(moment["excerpt"])

        print(f"  [{AGENT_NAME}] Title: {title}")

        # Upload if authenticated
        if youtube_service:
            try:
                yt_id = upload_to_youtube(youtube_service, clip_path, title, description, tags)
                uploaded.append({"youtube_id": yt_id, "title": title, "clip": clip_path})
            except Exception as e:
                print(f"  [{AGENT_NAME}] Upload failed: {e}")
                print(f"  [{AGENT_NAME}] Clip saved at: {clip_path}")
        else:
            print(f"  [{AGENT_NAME}] No YouTube auth — clip saved at: {clip_path}")

    # Cleanup full video
    try:
        os.remove(video_path)
    except OSError:
        pass

    return uploaded


def run_auto_mode(api_key):
    """Full auto: find trending -> clip -> upload."""
    print(f"\n{'='*50}")
    print(f"  {AGENT_NAME} Agent — AUTO MODE")
    print(f"{'='*50}")

    # Get YouTube upload service
    yt_service = get_youtube_upload_service()
    if not yt_service:
        print(f"  [{AGENT_NAME}] WARNING: Not authenticated for uploads.")
        print(f"  [{AGENT_NAME}] Clips will be saved locally only.")
        print(f"  [{AGENT_NAME}] Run 'python omar_agent.py --auth' to set up uploads.\n")

    # Find trending videos
    candidates = find_trending_videos(api_key)
    if not candidates:
        print(f"  [{AGENT_NAME}] No new videos found. Try again later.")
        return

    history = load_history()
    total_uploaded = 0

    for video in candidates[:3]:  # Process top 3 videos
        vid = video["video_id"]
        print(f"\n  [{AGENT_NAME}] Processing: {video['title']}")

        # Fetch transcript
        transcript, error = fetch_transcript(vid)
        if not transcript:
            print(f"  [{AGENT_NAME}] No transcript available. Skipping.")
            continue

        # Create session directory
        session_dir = os.path.join(OUTPUT_DIR, f"{vid}_{uuid.uuid4().hex[:6]}")
        os.makedirs(session_dir, exist_ok=True)

        # Process
        uploaded = process_video(vid, video["title"], transcript, session_dir, yt_service)
        total_uploaded += len(uploaded)

        # Mark as processed
        history["processed"].append(vid)
        for u in uploaded:
            history["uploaded"].append({
                "source": vid,
                "youtube_id": u["youtube_id"],
                "title": u["title"],
                "date": datetime.now().isoformat(),
            })
        save_history(history)

    print(f"\n{'='*50}")
    print(f"  [{AGENT_NAME}] Done! Uploaded {total_uploaded} shorts.")
    print(f"  [{AGENT_NAME}] Total videos processed: {len(history['processed'])}")
    print(f"{'='*50}\n")


def run_url_mode(url, api_key):
    """Process a specific URL."""
    print(f"\n{'='*50}")
    print(f"  {AGENT_NAME} Agent — URL MODE")
    print(f"{'='*50}")

    # Extract video ID
    match = re.search(r'(?:v=|youtu\.be/|embed/|shorts/)([a-zA-Z0-9_-]{11})', url)
    if not match:
        print(f"  [{AGENT_NAME}] Invalid YouTube URL.")
        return
    video_id = match.group(1)

    yt_service = get_youtube_upload_service()

    transcript, error = fetch_transcript(video_id)
    if not transcript:
        print(f"  [{AGENT_NAME}] No transcript: {error}")
        return

    session_dir = os.path.join(OUTPUT_DIR, f"{video_id}_{uuid.uuid4().hex[:6]}")
    os.makedirs(session_dir, exist_ok=True)

    process_video(video_id, "Custom Video", transcript, session_dir, yt_service)


def run_interactive():
    """Interactive menu."""
    print(f"\n{'='*50}")
    print(f"  {AGENT_NAME} Agent — AI YouTube Shorts Automation")
    print(f"{'='*50}")
    print()
    print(f"  1. Auto-find trending & upload Shorts")
    print(f"  2. Process a specific YouTube URL")
    print(f"  3. Set up YouTube OAuth2 (first time)")
    print(f"  4. View upload history")
    print(f"  5. Exit")
    print()

    choice = input("  Choose (1-5): ").strip()

    if choice == "1":
        api_key = _get_api_key()
        if api_key:
            run_auto_mode(api_key)
    elif choice == "2":
        url = input("  Paste YouTube URL: ").strip()
        api_key = _get_api_key()
        if url:
            run_url_mode(url, api_key)
    elif choice == "3":
        setup_oauth()
    elif choice == "4":
        history = load_history()
        print(f"\n  Videos processed: {len(history['processed'])}")
        print(f"  Shorts uploaded: {len(history['uploaded'])}")
        for u in history["uploaded"][-10:]:
            print(f"    - {u['title']} ({u['date'][:10]})")
        print()
    elif choice == "5":
        print(f"  [{AGENT_NAME}] Goodbye!")
        sys.exit(0)
    else:
        print(f"  [{AGENT_NAME}] Invalid choice.")


def _get_api_key():
    """Get YouTube API key from env or prompt."""
    key = os.environ.get("YOUTUBE_API_KEY", "")
    if not key:
        key_file = os.path.join(CREDS_DIR, "api_key.txt")
        if os.path.exists(key_file):
            with open(key_file) as f:
                key = f.read().strip()
        else:
            key = input("  Enter your YouTube Data API key: ").strip()
            if key:
                with open(key_file, "w") as f:
                    f.write(key)
                print(f"  [{AGENT_NAME}] API key saved to {key_file}")
    return key


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=f"{AGENT_NAME} Agent - YouTube Shorts Automation")
    parser.add_argument("--auto", action="store_true", help="Auto-find trending videos and upload")
    parser.add_argument("--url", type=str, help="Process a specific YouTube URL")
    parser.add_argument("--auth", action="store_true", help="Set up YouTube OAuth2 authentication")
    args = parser.parse_args()

    if args.auth:
        setup_oauth()
    elif args.auto:
        api_key = _get_api_key()
        if api_key:
            run_auto_mode(api_key)
    elif args.url:
        api_key = _get_api_key()
        run_url_mode(args.url, api_key)
    else:
        while True:
            run_interactive()
