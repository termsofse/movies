import os
import subprocess
import glob
from pathlib import Path

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

# Configuration
MOVIES_FULL_DIR = "movies-full"
MOVIES_OUTPUT_DIR = "movies"
CHUNK_SIZE_LIMIT_BYTES = 50 * 1024 * 1024  # 50 MB
SEGMENT_DURATION = 10  # HLS segment duration in seconds

def run_command(cmd, cwd=None):
    """Run a shell command and check for errors."""
    print(f"Running: {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    result = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error output:\n{result.stderr}")
        raise RuntimeError(f"Command failed with code {result.returncode}")
    return result.stdout

def check_dependencies():
    """Ensure ffmpeg and git are installed."""
    for tool in ["ffmpeg", "git"]:
        if not shutil_which(tool):
            raise EnvironmentError(f"Required tool '{tool}' is missing or not in PATH.")

def shutil_which(tool):
    from shutil import which
    return which(tool) is not None

def setup_gitignore():
    """Ensure movies-full/ is listed in .gitignore."""
    gitignore_path = Path(".gitignore")
    entries = []
    if gitignore_path.exists():
        entries = gitignore_path.read_text(encoding="utf-8").splitlines()
    
    cleaned_entries = [e.strip() for e in entries]
    if MOVIES_FULL_DIR not in cleaned_entries and f"/{MOVIES_FULL_DIR}" not in cleaned_entries:
        print(f"Adding {MOVIES_FULL_DIR}/ to .gitignore")
        with open(gitignore_path, "a", encoding="utf-8") as f:
            f.write(f"\n{MOVIES_FULL_DIR}/\n")

def generate_hls(input_video_path, output_dir):
    """Segment a movie into HLS (.m3u8 and .ts files) with a progress indicator."""
    output_dir.mkdir(parents=True, exist_ok=True)
    playlist_path = output_dir / "index.m3u8"
    
    print(f"🎬 Segmenting '{input_video_path.name}' into HLS chunks (this may take a moment)...")
    cmd = [
        "ffmpeg", "-y", "-i", str(input_video_path),
        "-c:v", "libx264", "-c:a", "aac",
        "-hls_time", str(SEGMENT_DURATION),
        "-hls_list_size", "0",
        "-hls_segment_filename", str(output_dir / "part%03d.ts"),
        str(playlist_path)
    ]
    run_command(cmd)
    return playlist_path

def generate_html(movie_title, output_dir):
    """Generate the required index.html file."""
    html_template = """<!DOCTYPE html>
<html>
<head>
    <title>Watching: {movie_title}</title>
    <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
    <style>
        body { background: #000; margin: 0; display: flex; justify-content: center; align-items: center; height: 100vh; }
        video { width: 90%; max-width: 90%; box-shadow: 0 0 20px rgba(0,0,0,0.5); }
    </style>
</head>
<body>
    <video id="video" controls></video>

    <script>
      var video = document.getElementById('video');
      var videoSrc = 'index.m3u8'; 

      if (Hls.isSupported()) {
        var hls = new Hls();
        hls.loadSource(videoSrc);
        hls.attachMedia(video);
      } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
        video.src = videoSrc;
      }
    </script>
</body>
</html>
"""
    html_content = html_template.replace("{movie_title}", movie_title)
    html_path = output_dir / "index.html"
    html_path.write_text(html_content, encoding="utf-8")

def group_files_by_size(file_paths, max_size):
    """Group files into batches where total size is roughly under max_size."""
    batches = []
    current_batch = []
    current_size = 0

    for file_path in sorted(file_paths):
        file_size = file_path.stat().st_size
        if current_size + file_size > max_size and current_batch:
            batches.append(current_batch)
            current_batch = [file_path]
            current_size = file_size
        else:
            current_batch.append(file_path)
            current_size += file_size

    if current_batch:
        batches.append(current_batch)
    return batches

def git_commit_and_push(batch_files, commit_message):
    """Stage, commit, and push a specific batch safely, skipping empty changes."""
    for f in batch_files:
        run_command(["git", "add", str(f)])
    
    # Check if there are actual changes staged to commit
    status = run_command(["git", "status", "--porcelain"])
    if not status.strip():
        print("⚠️ No changes to commit in this batch. Skipping.")
        return

    run_command(["git", "commit", "-m", commit_message])
    
    # Try pushing normally; if upstream branch is missing, set it automatically
    push_result = subprocess.run(["git", "push"], capture_output=True, text=True)
    if push_result.returncode != 0 and "no upstream branch" in push_result.stderr:
        print("🔗 Setting upstream branch for origin/main...")
        run_command(["git", "push", "--set-upstream", "origin", "main"])
    elif push_result.returncode != 0:
        print(f"Error output:\n{push_result.stderr}")
        raise RuntimeError(f"Git push failed with code {push_result.returncode}")

def process_movies():
    check_dependencies()
    setup_gitignore()

    full_dir = Path(MOVIES_FULL_DIR)
    out_base = Path(MOVIES_OUTPUT_DIR)
    out_base.mkdir(exist_ok=True)

    if not full_dir.exists():
        print(f"Directory '{MOVIES_FULL_DIR}' not found. Create it and place your source movies inside.")
        return

    extensions = ("*.mp4", "*.mkv", "*.avi", "*.mov")
    source_videos = []
    for ext in extensions:
        source_videos.extend(full_dir.glob(ext))

    if not source_videos:
        print(f"No source video files found in '{MOVIES_FULL_DIR}/'.")
        return

    print(f"\n🔍 Found {len(source_videos)} video(s) to inspect.")

    video_iterator = tqdm(source_videos, desc="Overall Progress") if HAS_TQDM else source_videos

    for video_path in video_iterator:
        movie_slug = video_path.stem.lower().replace(" ", "-")
        movie_out_dir = out_base / movie_slug

        if movie_out_dir.exists() and (movie_out_dir / "index.m3u8").exists():
            if not HAS_TQDM:
                print(f"⏭️ Skipping '{video_path.name}' — already processed.")
            continue

        print(f"\n▶️ Processing movie: {video_path.name}")

        generate_hls(video_path, movie_out_dir)

        movie_title = video_path.stem.replace("-", " ").title()
        generate_html(movie_title, movie_out_dir)

        all_movie_files = list(movie_out_dir.glob("*"))
        batches = group_files_by_size(all_movie_files, CHUNK_SIZE_LIMIT_BYTES)
        print(f"📦 Split into {len(batches)} batch(es) (~50MB each) for GitHub.")

        batch_iter = tqdm(enumerate(batches), total=len(batches), desc=f"Pushing {video_path.stem}") if HAS_TQDM else enumerate(batches)

        for i, batch in batch_iter:
            if not HAS_TQDM:
                print(f"🚀 Pushing batch {i+1}/{len(batches)}...")
            batch_rel_paths = [p.relative_to(Path(".")) for p in batch]
            git_commit_and_push(batch_rel_paths, f"Add batch {i+1} for {video_path.stem}")

    print("\n✨ All pending movies processed and pushed successfully!")

if __name__ == "__main__":
    process_movies()