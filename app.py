import os
import re
import uuid
import math
import shutil
import subprocess
from flask import Flask, request, jsonify, send_from_directory, render_template
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import TextFormatter

app = Flask(__name__)

# In-memory transcript cache keyed by video_id
_transcript_cache = {}

DOWNLOADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# Tool paths — resolved at startup so subprocess always finds them
FFMPEG_PATH = shutil.which("ffmpeg") or os.path.join(
    os.environ.get("LOCALAPPDATA", ""),
    "Microsoft", "WinGet", "Packages",
    "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe",
    "ffmpeg-8.1-full_build", "bin", "ffmpeg.exe",
)
YTDLP_PATH = shutil.which("yt-dlp") or "yt-dlp"

# ---------------------------------------------------------------------------
# Viral keyword / phrase dictionaries
# ---------------------------------------------------------------------------

VIRAL_KEYWORDS = {
    "shock": ["shocking", "unbelievable", "insane", "crazy", "mind-blowing", "wild",
              "unexpected", "plot twist", "jaw-dropping", "stunned", "speechless"],
    "secret": ["secret", "hidden", "revealed", "exposed", "truth", "nobody knows",
               "they don't want you to know", "cover-up", "leaked", "confidential"],
    "superlative": ["best", "worst", "greatest", "most", "never before", "first ever",
                    "only", "legendary", "ultimate", "record-breaking"],
    "urgency": ["breaking", "just happened", "right now", "urgent", "emergency",
                "happening now", "live", "update", "alert"],
    "emotion": ["hilarious", "heartbreaking", "terrifying", "amazing", "incredible",
                "beautiful", "devastating", "emotional", "inspiring", "disturbing"],
}

RHETORICAL_PATTERNS = [
    r"have you ever", r"did you know", r"can you believe", r"what if",
    r"how is this", r"why does nobody", r"who would have thought",
    r"isn't it", r"don't you think", r"wouldn't you",
]

ENGAGEMENT_HOOKS = [
    "so what happened was", "you won't believe", "here's the thing",
    "let me tell you", "wait for it", "watch this", "listen to this",
    "but here's the twist", "and then", "guess what", "check this out",
    "the crazy part is", "plot twist", "but wait", "hold on",
]

CLIFFHANGER_PHRASES = [
    "but then", "and suddenly", "out of nowhere", "everything changed",
    "that's when", "until", "and that's not all", "it gets worse",
    "it gets better", "the next thing",
]

INTENSITY_WORDS = [
    "love", "hate", "amazing", "incredible", "beautiful", "perfect",
    "terrible", "awful", "disgusting", "horrifying", "absolutely",
    "completely", "totally", "literally", "honestly", "seriously",
    "extremely", "ridiculously", "unreal", "impossible",
]


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def extract_video_id(url):
    """Extract the 11-character video ID from various YouTube URL formats."""
    patterns = [
        r'(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'(?:embed/)([a-zA-Z0-9_-]{11})',
        r'(?:shorts/)([a-zA-Z0-9_-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise ValueError(f"Could not extract video ID from URL: {url}")


def fetch_metadata(video_id, api_key):
    """Fetch video metadata via YouTube Data API v3."""
    if not api_key or api_key == "":
        return {
            "title": f"Video {video_id}",
            "channel": "Unknown",
            "views": "N/A",
            "duration": "N/A",
            "thumbnail": f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
            "description": "",
            "warning": "No API key provided — metadata is limited. Transcript analysis still works.",
        }
    try:
        from googleapiclient.discovery import build
        youtube = build("youtube", "v3", developerKey=api_key)
        resp = youtube.videos().list(
            part="snippet,statistics,contentDetails", id=video_id
        ).execute()

        if not resp.get("items"):
            return {"error": "Video not found via API", "title": video_id}

        item = resp["items"][0]
        snippet = item["snippet"]
        stats = item.get("statistics", {})
        return {
            "title": snippet.get("title", ""),
            "channel": snippet.get("channelTitle", ""),
            "views": stats.get("viewCount", "N/A"),
            "duration": item["contentDetails"].get("duration", ""),
            "thumbnail": snippet.get("thumbnails", {}).get("high", {}).get("url",
                          f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"),
            "description": snippet.get("description", "")[:500],
        }
    except Exception as e:
        return {
            "title": f"Video {video_id}",
            "channel": "Unknown",
            "views": "N/A",
            "duration": "N/A",
            "thumbnail": f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
            "description": "",
            "warning": f"API error: {str(e)}",
        }


def fetch_transcript(video_id):
    """Fetch the video transcript using youtube_transcript_api."""
    api = YouTubeTranscriptApi()
    try:
        result = api.fetch(video_id)
        transcript = [
            {"text": s.text, "start": s.start, "duration": s.duration}
            for s in result.snippets
        ]
        return transcript, None
    except Exception as e:
        # Try to get auto-generated captions in any language
        try:
            transcript_list = api.list(video_id)
            for t in transcript_list:
                try:
                    result = t.translate("en").fetch()
                    transcript = [
                        {"text": s.text, "start": s.start, "duration": s.duration}
                        for s in result.snippets
                    ]
                    return transcript, None
                except Exception:
                    result = t.fetch()
                    transcript = [
                        {"text": s.text, "start": s.start, "duration": s.duration}
                        for s in result.snippets
                    ]
                    return transcript, None
        except Exception:
            pass
        return [], str(e)


# ---------------------------------------------------------------------------
# Viral moment detection algorithm
# ---------------------------------------------------------------------------

def build_windows(transcript, min_duration=15, target_duration=30, max_duration=60):
    """Build overlapping sliding windows from transcript segments."""
    if not transcript:
        return []

    windows = []
    n = len(transcript)

    for i in range(n):
        start_time = transcript[i]["start"]
        text_parts = []
        segments_in_window = []
        end_time = start_time

        for j in range(i, n):
            seg = transcript[j]
            seg_end = seg["start"] + seg["duration"]
            window_duration = seg_end - start_time

            if window_duration > max_duration:
                break

            text_parts.append(seg["text"])
            segments_in_window.append(seg)
            end_time = seg_end

            if window_duration >= target_duration:
                break

        actual_duration = end_time - start_time
        if actual_duration < min_duration:
            continue

        windows.append({
            "start": start_time,
            "end": end_time,
            "duration": actual_duration,
            "text": " ".join(text_parts),
            "segments": segments_in_window,
        })

    return windows


def score_keyword_density(text):
    """Heuristic A: Viral keyword density (weight 0.25)."""
    words = text.lower().split()
    if not words:
        return 0.0

    keyword_count = 0
    text_lower = text.lower()
    for category, keywords in VIRAL_KEYWORDS.items():
        for kw in keywords:
            keyword_count += text_lower.count(kw)

    raw = (keyword_count / len(words)) * 100
    return min(raw / 10.0, 1.0)


def score_question_patterns(text):
    """Heuristic B: Question and rhetorical patterns (weight 0.20)."""
    question_count = text.count("?")

    rhetorical_count = 0
    text_lower = text.lower()
    for pattern in RHETORICAL_PATTERNS:
        rhetorical_count += len(re.findall(pattern, text_lower))

    raw = (question_count * 2 + rhetorical_count * 3)
    return min(raw / 10.0, 1.0)


def score_emotional_intensity(text):
    """Heuristic C: Emotional intensity (weight 0.25)."""
    words = text.split()
    if not words:
        return 0.0

    text_lower = text.lower()
    intensity_count = sum(1 for w in INTENSITY_WORDS if w in text_lower)
    exclamation_count = text.count("!")
    caps_count = sum(1 for w in words if w.isupper() and len(w) > 2)

    raw = (intensity_count + exclamation_count + caps_count) / len(words) * 100
    return min(raw / 10.0, 1.0)


def score_pacing(segments, global_mean_wps):
    """Heuristic D: Speech rate / pacing changes (weight 0.20)."""
    if not segments or global_mean_wps == 0:
        return 0.0

    local_wps_values = []
    for seg in segments:
        dur = seg["duration"]
        if dur > 0:
            wps = len(seg["text"].split()) / dur
            local_wps_values.append(wps)

    if not local_wps_values:
        return 0.0

    local_mean = sum(local_wps_values) / len(local_wps_values)
    deviation = abs(local_mean - global_mean_wps) / max(global_mean_wps, 0.001)

    # Variance within the window (speech rate changes = excitement)
    if len(local_wps_values) > 1:
        variance = sum((v - local_mean) ** 2 for v in local_wps_values) / len(local_wps_values)
        variance_score = min(math.sqrt(variance) / 2.0, 1.0)
    else:
        variance_score = 0.0

    raw = deviation + variance_score
    return min(raw / 2.0, 1.0)


def score_engagement_hooks(text):
    """Heuristic E: Engagement hooks & cliffhangers (weight 0.10)."""
    text_lower = text.lower()

    hook_count = sum(1 for hook in ENGAGEMENT_HOOKS if hook in text_lower)
    cliff_count = sum(1 for phrase in CLIFFHANGER_PHRASES if phrase in text_lower)

    raw = (hook_count + cliff_count) * 2
    return min(raw / 10.0, 1.0)


def get_top_heuristics(scores):
    """Return a human-readable reason based on which heuristics scored highest."""
    labels = {
        "keywords": "Viral keywords detected",
        "questions": "Rhetorical questions found",
        "emotion": "High emotional intensity",
        "pacing": "Exciting speech pacing",
        "hooks": "Strong engagement hooks",
    }
    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top = [labels[k] for k, v in sorted_scores[:2] if v > 0.1]
    if not top:
        return "General interest segment"
    return " + ".join(top)


def analyze_viral_moments(transcript):
    """Main analysis: score all windows and return top 5 non-overlapping moments."""
    if not transcript:
        return []

    # Calculate global mean words-per-second
    total_words = 0
    total_duration = 0
    for seg in transcript:
        total_words += len(seg["text"].split())
        total_duration += seg["duration"]
    global_mean_wps = total_words / max(total_duration, 0.001)

    # Build candidate windows
    windows = build_windows(transcript)
    if not windows:
        return []

    # Score each window
    scored = []
    for w in windows:
        s_kw = score_keyword_density(w["text"])
        s_q = score_question_patterns(w["text"])
        s_em = score_emotional_intensity(w["text"])
        s_pc = score_pacing(w["segments"], global_mean_wps)
        s_hk = score_engagement_hooks(w["text"])

        final = (s_kw * 0.25) + (s_q * 0.20) + (s_em * 0.25) + (s_pc * 0.20) + (s_hk * 0.10)

        heuristic_scores = {
            "keywords": s_kw,
            "questions": s_q,
            "emotion": s_em,
            "pacing": s_pc,
            "hooks": s_hk,
        }

        scored.append({
            "start": round(w["start"], 2),
            "end": round(w["end"], 2),
            "duration": round(w["duration"], 2),
            "score": round(final * 100, 1),
            "reason": get_top_heuristics(heuristic_scores),
            "excerpt": w["text"][:150] + ("..." if len(w["text"]) > 150 else ""),
            "heuristics": {k: round(v, 3) for k, v in heuristic_scores.items()},
        })

    # Sort by score descending
    scored.sort(key=lambda x: x["score"], reverse=True)

    # Select top 5 non-overlapping
    selected = []
    for candidate in scored:
        if len(selected) >= 5:
            break
        overlap = False
        for s in selected:
            if not (candidate["end"] <= s["start"] + 5 or candidate["start"] >= s["end"] - 5):
                overlap = True
                break
        if not overlap:
            selected.append(candidate)

    # Sort selected by start time for logical ordering
    selected.sort(key=lambda x: x["start"])

    # Add clip labels
    for i, clip in enumerate(selected):
        clip["label"] = f"Clip {i + 1}"

    return selected


# ---------------------------------------------------------------------------
# Video download & clip extraction
# ---------------------------------------------------------------------------

def download_video(video_id, output_dir):
    """Download YouTube video using yt-dlp."""
    output_path = os.path.join(output_dir, f"{video_id}.mp4")
    url = f"https://www.youtube.com/watch?v={video_id}"

    cmd = [
        YTDLP_PATH,
        "-f", "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", output_path,
        "--no-playlist",
        "--no-warnings",
        "--ffmpeg-location", os.path.dirname(FFMPEG_PATH),
        url,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr[:500]}")

    # yt-dlp might add extensions, find the actual file
    if os.path.exists(output_path):
        return output_path

    # Check for common alternative names
    for f in os.listdir(output_dir):
        if f.startswith(video_id) and f.endswith(".mp4"):
            return os.path.join(output_dir, f)

    raise FileNotFoundError(f"Downloaded video not found in {output_dir}")


def generate_ass_subtitles(transcript, clip_start, clip_end, ass_path):
    """Generate an ASS subtitle file for a clip from transcript segments.

    Captions are styled TikTok-style: bold white text with black outline,
    positioned at the bottom-center of the screen, one short phrase at a time.
    """
    # ASS header with TikTok-style formatting
    header = """[Script Info]
Title: OMARQI Captions
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

        # Skip segments outside clip range
        if seg_end <= clip_start or seg_start >= clip_end:
            continue

        # Adjust times relative to clip start
        rel_start = max(seg_start - clip_start, 0)
        rel_end = min(seg_end - clip_start, clip_end - clip_start)

        text = seg["text"].strip()
        if not text:
            continue

        # Split long text into chunks of ~4 words for TikTok-style word-by-word feel
        words = text.split()
        if len(words) > 5:
            chunk_duration = (rel_end - rel_start) / max(math.ceil(len(words) / 4), 1)
            for c in range(0, len(words), 4):
                chunk = " ".join(words[c:c + 4])
                c_start = rel_start + (c / max(len(words), 1)) * (rel_end - rel_start)
                c_end = min(c_start + chunk_duration, rel_end)
                events.append((c_start, c_end, chunk.upper()))
        else:
            events.append((rel_start, rel_end, text.upper()))

    # Write ASS file
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(header)
        for start_t, end_t, text in events:
            s = format_ass_time(start_t)
            e = format_ass_time(end_t)
            # Escape special ASS characters
            clean_text = text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
            f.write(f"Dialogue: 0,{s},{e},Default,,0,0,0,,{clean_text}\n")

    return ass_path


def format_ass_time(seconds):
    """Convert seconds to ASS timestamp format H:MM:SS.CC"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def cut_clip(input_path, start, end, output_path, transcript=None):
    """Cut a clip in TikTok/Shorts 9:16 vertical format (1080x1920) with burned-in captions."""
    duration = end - start

    # Generate subtitle file if transcript available
    ass_path = output_path.replace(".mp4", ".ass")
    has_subs = False
    if transcript:
        generate_ass_subtitles(transcript, start, end, ass_path)
        has_subs = os.path.exists(ass_path)

    # Build video filter chain
    # Escape path for ffmpeg filter (Windows backslashes and colons)
    if has_subs:
        escaped_ass = ass_path.replace("\\", "/").replace(":", "\\:")
        vf = (
            f"crop=ih*9/16:ih,"
            f"scale=1080:1920,"
            f"setsar=1,"
            f"ass='{escaped_ass}'"
        )
    else:
        vf = (
            "crop=ih*9/16:ih,"
            "scale=1080:1920,"
            "setsar=1"
        )

    cmd = [
        FFMPEG_PATH, "-y",
        "-ss", str(start),
        "-i", input_path,
        "-t", str(duration),
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "44100",
        "-movflags", "+faststart",
        "-pix_fmt", "yuv420p",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    # Clean up subtitle file
    if has_subs:
        try:
            os.remove(ass_path)
        except OSError:
            pass

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr[:500]}")

    return output_path


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json()
    if not data or "url" not in data:
        return jsonify({"error": "Missing 'url' in request body"}), 400

    url = data["url"]
    api_key = data.get("api_key", "")

    try:
        video_id = extract_video_id(url)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    metadata = fetch_metadata(video_id, api_key)
    transcript, error = fetch_transcript(video_id)

    if not transcript:
        return jsonify({
            "error": f"Could not fetch transcript: {error or 'No captions available'}. "
                     "This video may not have captions enabled.",
            "video_id": video_id,
            "metadata": metadata,
        }), 400

    moments = analyze_viral_moments(transcript)

    if not moments:
        return jsonify({
            "error": "Could not identify viral moments in this video. "
                     "The transcript may be too short.",
            "video_id": video_id,
            "metadata": metadata,
        }), 400

    # Cache transcript so /extract can use it for captions
    _transcript_cache[video_id] = transcript

    return jsonify({
        "video_id": video_id,
        "metadata": metadata,
        "moments": moments,
        "transcript_length": len(transcript),
    })


@app.route("/extract", methods=["POST"])
def extract():
    data = request.get_json()
    if not data or "video_id" not in data or "moments" not in data:
        return jsonify({"error": "Missing 'video_id' or 'moments'"}), 400

    video_id = data["video_id"]
    moments = data["moments"]

    # Get cached transcript for caption syncing
    transcript = _transcript_cache.get(video_id, [])

    session_id = uuid.uuid4().hex[:12]
    session_dir = os.path.join(DOWNLOADS_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)

    try:
        # Download the full video
        video_path = download_video(video_id, session_dir)

        # Cut each clip with synced captions
        clips = []
        for i, moment in enumerate(moments):
            clip_name = f"clip_{i + 1}.mp4"
            clip_path = os.path.join(session_dir, clip_name)
            cut_clip(video_path, moment["start"], moment["end"], clip_path, transcript=transcript)
            clips.append({
                "filename": clip_name,
                "label": moment.get("label", f"Clip {i + 1}"),
                "start": moment["start"],
                "end": moment["end"],
            })

        # Delete the full video to save space
        try:
            os.remove(video_path)
        except OSError:
            pass

        return jsonify({
            "session_id": session_id,
            "clips": clips,
        })

    except Exception as e:
        # Clean up on failure
        shutil.rmtree(session_dir, ignore_errors=True)
        return jsonify({"error": f"Extraction failed: {str(e)}"}), 500


@app.route("/download/<session_id>/<filename>")
def download(session_id, filename):
    # Sanitize to prevent directory traversal
    safe_session = re.sub(r'[^a-zA-Z0-9]', '', session_id)
    safe_filename = os.path.basename(filename)
    directory = os.path.join(DOWNLOADS_DIR, safe_session)

    if not os.path.isdir(directory):
        return jsonify({"error": "Session not found"}), 404

    return send_from_directory(directory, safe_filename, as_attachment=True)


@app.route("/cleanup/<session_id>", methods=["POST"])
def cleanup(session_id):
    safe_session = re.sub(r'[^a-zA-Z0-9]', '', session_id)
    session_dir = os.path.join(DOWNLOADS_DIR, safe_session)

    if os.path.isdir(session_dir):
        shutil.rmtree(session_dir, ignore_errors=True)
        return jsonify({"message": "Cleaned up successfully"})

    return jsonify({"message": "Nothing to clean up"})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print()
    print("  OMARQI - AI Viral Clip Generator")
    print("  --------------------------------")
    print("  Open http://localhost:5000 in your browser")
    print()
    app.run(debug=True, host="0.0.0.0", port=5000)
