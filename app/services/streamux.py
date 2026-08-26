"""
StreamUx — one published RTSP/SRT encode for ATAK.

Fat ingest stays on {name}__src (stream copy). The path ATAK pulls ({name})
is a single profile: low / medium / high — unless Encoding is off, in which
case {name} is a cheap -c copy passthrough of {name}__src (no x264). Switching
profile or encoding restarts only the StreamUx ffmpeg; the camera pull stays up.

This is not ABR HLS. HLS ABR stays in Settings for browser players.
"""
import json
import logging
import os
import re
import signal
import subprocess
import threading
import time

from app.config import DATA_DIR, LOGS_DIR, MEDIAMTX_RTSP_URL, MEDIAMTX_API_URL
from app.services.mediamtx import MediaMTXClient

logger = logging.getLogger(__name__)

SRC_SUFFIX = '__src'
DEFAULT_PROFILE = 'medium'
STATE_FILE = os.path.join(DATA_DIR, 'streamux_profiles.json')
LEGACY_STATE_FILE = os.path.join(DATA_DIR, 'overview_rungs.json')
OVERLAY_STATE_FILE = os.path.join(DATA_DIR, 'streamux_overlay.json')
LEGACY_OVERLAY_STATE_FILE = os.path.join(DATA_DIR, 'overview_overlay.json')
OVERLAY_DIR = os.path.join(DATA_DIR, 'streamux-overlay')
LEGACY_OVERLAY_DIR = os.path.join(DATA_DIR, 'overview-overlay')
FFMPEG_LOG_DIR = os.path.join(LOGS_DIR, 'ffmpeg')
FONT_CANDIDATES = (
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
    '/usr/share/fonts/truetype/freefont/FreeSans.ttf',
    '/opt/app/fonts/DejaVuSans.ttf',
)

# Old ids from the first ladder; rewrite on load so state/API do not keep them.
PROFILE_ALIASES = {
    'floor': 'low',
    'mid': 'medium',
    'g2g': 'high',
}

# ATAK Video wants H.264 on one RTSP/SRT URL. Low is 5 fps H.264, not MJPEG.
PROFILES = {
    'low': {
        'id': 'low',
        'label': 'Low',
        'detail': 'Always usable on a bad link',
        'budget': '5 fps · 426p · ~100 kbps H.264',
        'width': 426,
        'height': 240,
        'fps': 5,
        'video_bitrate': '100k',
        'max_rate': '140k',
        'buf_size': '200k',
        'gop': 5,
        'audio': False,
        'h264_profile': 'baseline',
        'level': '3.0',
    },
    'medium': {
        'id': 'medium',
        'label': 'Medium',
        'detail': 'Constrained mesh / LTE',
        'budget': '15 fps · 480p · ~400 kbps H.264',
        'width': 854,
        'height': 480,
        'fps': 15,
        'video_bitrate': '400k',
        'max_rate': '500k',
        'buf_size': '800k',
        'gop': 15,
        'audio': False,
        'h264_profile': 'baseline',
        'level': '3.1',
    },
    'high': {
        'id': 'high',
        'label': 'High',
        'detail': 'Healthy Silvus / LAN',
        'budget': '30 fps · 720p · ~1.5 Mbps H.264',
        'width': 1280,
        'height': 720,
        'fps': 30,
        'video_bitrate': '1500k',
        'max_rate': '1800k',
        'buf_size': '3M',
        'gop': 30,
        'audio': False,
        'h264_profile': 'main',
        'level': '4.0',
    },
}

mediamtx = MediaMTXClient(MEDIAMTX_API_URL)


class EncodingOff(Exception):
    """Profile encoder is disabled; published path is a copy of ingest."""


def _flag_bool(value, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    return bool(value)


def parse_state_entry(value):
    """Load one streamux_profiles.json value. Missing encoding = on.

    Legacy: "medium" or "floor". Current: {"profile": "medium", "encoding": true}.
    Returns (profile, encoding, changed) or None.
    """
    if isinstance(value, str):
        nv = normalize_profile(value)
        if nv not in PROFILES:
            return None
        return nv, True, True
    if isinstance(value, dict):
        raw = value.get('profile')
        nv = normalize_profile('' if raw is None else str(raw))
        if nv not in PROFILES:
            return None
        if 'encoding' not in value:
            return nv, True, True
        enc = _flag_bool(value.get('encoding'), True)
        changed = nv != raw or not isinstance(value.get('encoding'), bool)
        return nv, enc, changed
    return None


def normalize_profile(profile: str) -> str:
    p = (profile or '').strip().lower()
    return PROFILE_ALIASES.get(p, p)


def source_name(stream_name: str) -> str:
    return f'{stream_name}{SRC_SUFFIX}'


def is_source_path(path_name: str) -> bool:
    return bool(path_name) and path_name.endswith(SRC_SUFFIX)


def profile_catalog() -> list:
    return [
        {
            'id': p['id'],
            'label': p['label'],
            'detail': p['detail'],
            'budget': p['budget'],
        }
        for p in (PROFILES['low'], PROFILES['medium'], PROFILES['high'])
    ]


def find_font() -> str:
    for path in FONT_CANDIDATES:
        if os.path.isfile(path):
            return path
    return ''


def _ffmpeg_filter_path(path: str) -> str:
    return path.replace('\\', '/').replace(':', '\\:').replace("'", r"\'")


def _format_overlay_bitrate(raw: str) -> str:
    s = (raw or '').strip()
    if not s or s.upper() in ('N/A', 'NA'):
        return '-- kbps'
    m = re.match(r'([0-9.]+)\s*([kmgKMG])?bits/s', s)
    if not m:
        return s.replace('bits/s', 'bps')
    n = float(m.group(1))
    unit = (m.group(2) or '').lower()
    if unit == 'k':
        kbps = n
    elif unit == 'm':
        kbps = n * 1000.0
    elif unit == 'g':
        kbps = n * 1_000_000.0
    else:
        kbps = n / 1000.0
    if kbps >= 1000:
        return f'{kbps / 1000.0:.1f} Mbps'
    return f'{kbps:.0f} kbps'


def _format_overlay_fps(raw: str) -> str:
    try:
        fps = float(raw)
    except (TypeError, ValueError):
        return '-- fps'
    if fps >= 10:
        return f'{fps:.0f} fps'
    if fps >= 1:
        return f'{fps:.1f} fps'
    return f'{fps:.2f} fps'


def _safe_stream_token(stream_name: str) -> str:
    """Basename-safe token. Same rules as overlay text files — never path-traverse."""
    return re.sub(r'[^A-Za-z0-9._-]+', '_', stream_name or '').strip('_') or 'stream'


def _overlay_text_path(stream_name: str) -> str:
    return os.path.join(OVERLAY_DIR, f'{_safe_stream_token(stream_name)}.txt')


ENCODER_LOG_MAX_BYTES = 512 * 1024


def _encoder_log_write_path(stream_name: str) -> str:
    return os.path.join(FFMPEG_LOG_DIR, f'streamux-{_safe_stream_token(stream_name)}.log')


def _rotate_encoder_log_if_huge(log_path: str):
    """Wipe only when the file is huge. Keep a short tail so the UI is not empty."""
    try:
        if os.path.getsize(log_path) <= ENCODER_LOG_MAX_BYTES:
            return
    except OSError:
        return
    tail = _tail_lines(log_path, 80)
    try:
        with open(log_path, 'w', encoding='utf-8', errors='replace') as f:
            if tail:
                f.write('\n'.join(tail) + '\n')
            f.write('--- log rotated ---\n')
    except OSError:
        pass


def _open_encoder_log(stream_name: str):
    """Append to streamux-<name>.log for every spawn. Never truncate on restart."""
    os.makedirs(FFMPEG_LOG_DIR, exist_ok=True)
    log_path = _encoder_log_write_path(stream_name)
    _rotate_encoder_log_if_huge(log_path)
    return open(log_path, 'a', encoding='utf-8', errors='replace', buffering=1)


def encoder_log_file(stream_name: str) -> str | None:
    """streamux-<name>.log, else legacy overview-<name>.log. Stays under FFMPEG_LOG_DIR."""
    safe = _safe_stream_token(stream_name)
    try:
        base = os.path.realpath(FFMPEG_LOG_DIR)
    except OSError:
        return None
    for prefix in ('streamux', 'overview'):
        candidate = os.path.realpath(os.path.join(FFMPEG_LOG_DIR, f'{prefix}-{safe}.log'))
        try:
            common = os.path.commonpath([base, candidate])
        except ValueError:
            continue
        if common != base:
            continue
        if os.path.isfile(candidate):
            return candidate
    return None


def _tail_lines(log_path: str, n: int) -> list:
    """Last n lines, reading from EOF. Does not load the whole file unless it is tiny."""
    n = max(1, int(n))
    try:
        with open(log_path, 'rb') as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size == 0:
                return []
            block = 4096
            data = b''
            remaining = size
            while remaining > 0 and data.count(b'\n') <= n:
                step = min(block, remaining)
                remaining -= step
                f.seek(remaining)
                data = f.read(step) + data
                if len(data) >= 256 * 1024:
                    break
            text = data.decode('utf-8', errors='replace')
            if remaining > 0:
                nl = text.find('\n')
                if nl != -1:
                    text = text[nl + 1:]
            return text.splitlines()[-n:]
    except OSError:
        return []


def _atomic_write(path: str, text: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='ascii', errors='replace') as f:
        f.write(text)
    os.replace(tmp, path)


def _pick_persist_path(new_path: str, old_path: str):
    if os.path.isfile(new_path):
        return new_path
    if os.path.isfile(old_path):
        return old_path
    return None


class StreamuxManager:
    """One transcode process per public stream name."""

    def __init__(self):
        self._lock = threading.Lock()
        self._profiles: dict = {}
        self._encoding: dict = {}
        self._overlays: dict = {}
        self._procs: dict = {}
        self._stderr: dict = {}
        self._errors: dict = {}
        self._stopping: set = set()
        self._generations: dict = {}
        self._ensure_locks: dict = {}
        self._migrate_overlay_dir()
        self._load()
        self._load_overlays()

    def _migrate_overlay_dir(self):
        if os.path.isdir(OVERLAY_DIR):
            return
        if os.path.isdir(LEGACY_OVERLAY_DIR):
            try:
                os.makedirs(os.path.dirname(OVERLAY_DIR), exist_ok=True)
                os.rename(LEGACY_OVERLAY_DIR, OVERLAY_DIR)
                logger.info('streamux: migrated overlay dir overview-overlay -> streamux-overlay')
            except OSError as e:
                logger.warning(f"streamux: could not migrate overlay dir: {e}")

    def _load(self):
        src = _pick_persist_path(STATE_FILE, LEGACY_STATE_FILE)
        if not src:
            return
        try:
            with open(src, 'r') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return
            profiles = {}
            encoding = {}
            id_changed = False
            format_changed = False
            for k, v in data.items():
                parsed = parse_state_entry(v)
                if not parsed:
                    continue
                nv, enc, entry_changed = parsed
                if isinstance(v, str) and normalize_profile(v) != v:
                    id_changed = True
                if isinstance(v, dict) and normalize_profile(str(v.get('profile') or '')) != v.get('profile'):
                    id_changed = True
                if entry_changed:
                    format_changed = True
                profiles[k] = nv
                encoding[k] = enc
            self._profiles = profiles
            self._encoding = encoding
            if id_changed:
                logger.info('streamux: migrated profile ids to low/medium/high')
            if src != STATE_FILE:
                logger.info('streamux: migrated overview_rungs.json -> streamux_profiles.json')
            if id_changed or format_changed or src != STATE_FILE:
                self._save()
        except Exception as e:
            logger.warning(f"streamux: could not load {src}: {e}")

    def _save(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with self._lock:
                payload = {}
                names = set(self._profiles) | set(self._encoding)
                for name in names:
                    payload[name] = {
                        'profile': self._profiles.get(name, DEFAULT_PROFILE),
                        'encoding': bool(self._encoding.get(name, True)),
                    }
            tmp = STATE_FILE + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(payload, f)
            os.replace(tmp, STATE_FILE)
        except Exception as e:
            logger.error(f"streamux: could not save profiles: {e}")

    def _load_overlays(self):
        src = _pick_persist_path(OVERLAY_STATE_FILE, LEGACY_OVERLAY_STATE_FILE)
        if not src:
            return
        try:
            with open(src, 'r') as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._overlays = {
                    str(k): bool(v) for k, v in data.items()
                }
            if src != OVERLAY_STATE_FILE:
                logger.info('streamux: migrated overview_overlay.json -> streamux_overlay.json')
                self._save_overlays()
        except Exception as e:
            logger.warning(f"streamux: could not load {src}: {e}")

    def _save_overlays(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            tmp = OVERLAY_STATE_FILE + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(self._overlays, f)
            os.replace(tmp, OVERLAY_STATE_FILE)
        except Exception as e:
            logger.error(f"streamux: could not save overlay flags: {e}")

    def get_profile(self, stream_name: str) -> str:
        with self._lock:
            return self._profiles.get(stream_name, DEFAULT_PROFILE)

    def get_encoding(self, stream_name: str) -> bool:
        """Missing key = encoding on (today's behaviour)."""
        with self._lock:
            return bool(self._encoding.get(stream_name, True))

    def _bump_gen(self, stream_name: str) -> int:
        with self._lock:
            n = int(self._generations.get(stream_name, 0)) + 1
            self._generations[stream_name] = n
            return n

    def _current_gen(self, stream_name: str) -> int:
        with self._lock:
            return int(self._generations.get(stream_name, 0))

    def _op_lock(self, stream_name: str) -> threading.Lock:
        with self._lock:
            lock = self._ensure_locks.get(stream_name)
            if lock is None:
                lock = threading.Lock()
                self._ensure_locks[stream_name] = lock
            return lock

    def get_overlay(self, stream_name: str) -> bool:
        with self._lock:
            return bool(self._overlays.get(stream_name, False))

    def status(self, stream_name: str) -> dict:
        profile = self.get_profile(stream_name)
        overlay = self.get_overlay(stream_name)
        encoding = self.get_encoding(stream_name)
        with self._lock:
            proc = self._procs.get(stream_name)
            running = bool(proc and proc.poll() is None)
            last_error = self._errors.get(stream_name, '')
        src = source_name(stream_name)
        src_path = mediamtx.get_path(src) or {}
        pub_path = mediamtx.get_path(stream_name) or {}
        published = bool(pub_path.get('ready'))
        ingest = bool(src_path.get('ready'))
        if ingest and not published:
            if not last_error:
                if encoding:
                    last_error = 'ATAK path is down (404 / not ready). Ingest is still up. Restart the encoder.'
                else:
                    last_error = (
                        'Passthrough is down. Ingest is still up. '
                        'Published URL is a copy of the source when the copy process is running.'
                    )
        return {
            'name': stream_name,
            'profile': profile,
            'overlay': overlay,
            'encoding': encoding,
            'mode': 'encode' if encoding else 'passthrough',
            'running': running,
            'sourcePath': src,
            'sourceReady': ingest,
            'publishedReady': published,
            'lastError': last_error,
        }

    def read_encoder_log(self, stream_name: str, lines: int = 100) -> dict:
        """Tail the encoder log. On-demand (and open-panel poll) — not part of status list."""
        try:
            n = int(lines)
        except (TypeError, ValueError):
            n = 100
        n = max(1, min(n, 100))
        with self._lock:
            last_error = self._errors.get(stream_name, '')
        path = encoder_log_file(stream_name)
        return {
            'name': stream_name,
            'lastError': last_error,
            'lines': _tail_lines(path, n) if path else [],
        }

    def list_status(self, stream_names: list) -> list:
        return [self.status(n) for n in stream_names]

    def start(self, stream_name: str, wait: bool = False):
        """Ensure published-path ffmpeg is running (profile encode or passthrough)."""
        if wait:
            self._ensure(stream_name)
            return
        threading.Thread(
            target=self._ensure,
            args=(stream_name,),
            daemon=True,
            name=f'streamux-{stream_name}',
        ).start()

    def set_profile(self, stream_name: str, profile: str, force: bool = False) -> dict:
        return self.update(stream_name, profile=profile, force=force)

    def set_overlay(self, stream_name: str, enabled: bool) -> dict:
        return self.update(stream_name, overlay=bool(enabled))

    def set_encoding(self, stream_name: str, enabled: bool) -> dict:
        return self.update(stream_name, encoding=bool(enabled))

    def update(self, stream_name: str, profile: str | None = None, overlay: bool | None = None,
               encoding: bool | None = None, force: bool = False) -> dict:
        if profile is not None:
            profile = normalize_profile(profile)
            if profile not in PROFILES:
                raise ValueError(f'Unknown profile: {profile}')
        pub = (mediamtx.get_path(stream_name) or {}).get('ready')
        with self._lock:
            current_profile = self._profiles.get(stream_name, DEFAULT_PROFILE)
            current_overlay = bool(self._overlays.get(stream_name, False))
            current_encoding = bool(self._encoding.get(stream_name, True))
            new_profile = current_profile if profile is None else profile
            new_overlay = current_overlay if overlay is None else bool(overlay)
            new_encoding = current_encoding if encoding is None else bool(encoding)
            proc = self._procs.get(stream_name)
            alive = bool(proc and proc.poll() is None)
            self._profiles[stream_name] = new_profile
            self._overlays[stream_name] = new_overlay
            self._encoding[stream_name] = new_encoding
        self._save()
        self._save_overlays()
        encoding_changed = current_encoding != new_encoding
        if new_encoding:
            need_restart = (
                force
                or encoding_changed
                or not alive
                or not pub
                or current_profile != new_profile
                or current_overlay != new_overlay
            )
        else:
            # Overlay cannot burn on -c copy. Do not spawn x264 because overlay
            # or profile changed, or because the published path is briefly down.
            need_restart = force or encoding_changed or not alive
        if need_restart:
            logger.info(
                f"streamux {stream_name}: profile {current_profile} -> {new_profile} "
                f"overlay {current_overlay} -> {new_overlay} "
                f"encoding {current_encoding} -> {new_encoding} force={force}"
            )
            self._restart(stream_name)
        from app.websocket.broadcast import broadcast
        st = self.status(stream_name)
        broadcast('streamux_profile', st)
        return st

    def restart(self, stream_name: str) -> dict:
        if not self.get_encoding(stream_name):
            raise EncodingOff(
                'Encoding is off. Turn encoding on to restart the profile encoder. '
                'The published URL is a copy of ingest.'
            )
        return self.update(stream_name, force=True)

    def stop(self, stream_name: str):
        self._bump_gen(stream_name)
        self._kill(stream_name)
        src = source_name(stream_name)
        try:
            mediamtx.delete_path(src)
        except Exception:
            pass

    def _ensure(self, stream_name: str, gen: int | None = None):
        expected = self._current_gen(stream_name) if gen is None else int(gen)
        if expected != self._current_gen(stream_name):
            logger.info(f"streamux {stream_name}: stale ensure, not spawning")
            return
        if not self._wait_source(stream_name):
            logger.warning(f"streamux {stream_name}: source not ready, encoder not started")
            return
        if expected != self._current_gen(stream_name):
            logger.info(
                f"streamux {stream_name}: stale ensure after source wait, not spawning"
            )
            return
        with self._op_lock(stream_name):
            if expected != self._current_gen(stream_name):
                return
            with self._lock:
                proc = self._procs.get(stream_name)
                keep_pid = proc.pid if proc and proc.poll() is None else None
            self._reap_orphans(stream_name, keep_pid=keep_pid)
            if keep_pid is not None:
                return
            self._spawn(stream_name, expected)

    def _restart(self, stream_name: str):
        gen = self._bump_gen(stream_name)
        self._kill(stream_name)
        self._ensure(stream_name, gen=gen)

    def _wait_source(self, stream_name: str, timeout: float = 45.0) -> bool:
        src = source_name(stream_name)
        deadline = time.time() + timeout
        while time.time() < deadline:
            path = mediamtx.get_path(src) or {}
            if path.get('ready'):
                return True
            time.sleep(0.5)
        return False

    def _spawn(self, stream_name: str, expected: int | None = None):
        if expected is not None and expected != self._current_gen(stream_name):
            logger.info(f"streamux {stream_name}: stale spawn, skipped")
            return
        encoding = self.get_encoding(stream_name)
        profile_id = self.get_profile(stream_name)
        mode = 'encode' if encoding else 'passthrough'
        font_error = ''
        use_overlay = False
        if encoding:
            want_overlay = self.get_overlay(stream_name)
            font = find_font() if want_overlay else ''
            use_overlay = bool(want_overlay and font)
            if want_overlay and not font:
                font_error = (
                    'Overlay is on, but no font is in the container. '
                    'Install fonts-dejavu-core (or copy a TTF to /opt/app/fonts). '
                    'Encoder is running without the overlay.'
                )
            if use_overlay:
                self._seed_overlay_text(stream_name, profile_id)
            cmd = self._build_cmd(stream_name, profile_id, overlay=use_overlay, font=font)
            spawn_note = f"profile={profile_id} overlay={'on' if use_overlay else 'off'}"
        else:
            cmd = self._build_passthrough_cmd(stream_name)
            spawn_note = 'mode=passthrough encoding=off'
        if expected is not None and expected != self._current_gen(stream_name):
            logger.info(f"streamux {stream_name}: stale spawn after build, skipped")
            return
        if self.get_encoding(stream_name) != encoding:
            logger.info(
                f"streamux {stream_name}: encoding flipped before spawn, "
                f"not starting {mode}"
            )
            return
        spawn_gen = expected if expected is not None else self._current_gen(stream_name)
        log_path = _encoder_log_write_path(stream_name)
        try:
            # Append for every stream. 'w' wiped the file on overlay/profile restart
            # (healthy encode + loglevel warning → 0 bytes / a few leftover lines).
            stderr_file = _open_encoder_log(stream_name)
            stderr_file.write(
                f"--- streamux spawn {time.strftime('%Y-%m-%dT%H:%M:%S')} "
                f"{spawn_note} ---\n"
            )
            stderr_file.flush()
        except OSError as e:
            logger.error(f"streamux {stream_name}: cannot open log {log_path}: {e}")
            stderr_file = subprocess.DEVNULL

        mediamtx.add_path(stream_name, {
            'source': 'publisher',
            'overridePublisher': True,
        })

        if (
            (expected is not None and expected != self._current_gen(stream_name))
            or self.get_encoding(stream_name) != encoding
        ):
            logger.info(f"streamux {stream_name}: spawn cancelled after path add")
            if hasattr(stderr_file, 'write'):
                try:
                    stderr_file.write('--- streamux spawn cancelled ---\n')
                    stderr_file.flush()
                except OSError:
                    pass
            if hasattr(stderr_file, 'close'):
                try:
                    stderr_file.close()
                except Exception:
                    pass
            return

        stdout = subprocess.PIPE if use_overlay else subprocess.DEVNULL
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr_file,
                start_new_session=True,
                bufsize=0 if use_overlay else -1,
            )
        except Exception as e:
            logger.error(f"streamux {stream_name}: ffmpeg start failed: {e}")
            if hasattr(stderr_file, 'close'):
                stderr_file.close()
            return

        with self._lock:
            old = self._stderr.pop(stream_name, None)
            if old and hasattr(old, 'close'):
                try:
                    old.close()
                except Exception:
                    pass
            self._procs[stream_name] = proc
            self._stderr[stream_name] = stderr_file
            self._errors.pop(stream_name, None)
            if font_error:
                self._errors[stream_name] = font_error
            self._stopping.discard(stream_name)
        if use_overlay:
            threading.Thread(
                target=self._read_progress,
                args=(stream_name, proc),
                daemon=True,
                name=f'streamux-progress-{stream_name}',
            ).start()
        logger.info(
            f"streamux {stream_name}: {mode} started profile={profile_id} "
            f"overlay={use_overlay} pid={proc.pid}"
        )
        threading.Thread(
            target=self._watch,
            args=(stream_name, proc, profile_id, mode, spawn_gen),
            daemon=True,
            name=f'streamux-watch-{stream_name}',
        ).start()

    def _watch(self, stream_name: str, proc: subprocess.Popen, profile_id: str,
               mode: str = 'encode', gen: int = 0):
        rc = proc.wait()
        with self._lock:
            fh = self._stderr.get(stream_name)
        if fh and hasattr(fh, 'flush'):
            try:
                fh.flush()
            except Exception:
                pass
        log_path = encoder_log_file(stream_name) or _encoder_log_write_path(stream_name)
        tail = self._log_tail(log_path)
        with self._lock:
            current = self._procs.get(stream_name)
            still_ours = current is proc
            stopping = stream_name in self._stopping
            if still_ours:
                self._procs.pop(stream_name, None)
            if still_ours and not stopping:
                self._errors[stream_name] = (
                    f'encoder exited {rc}. {tail}'.strip()
                    or f'encoder exited {rc}'
                )
        if still_ours and not stopping:
            if int(gen) != self._current_gen(stream_name):
                logger.info(
                    f"streamux {stream_name}: ffmpeg {mode} exited {rc}, "
                    f"generation moved, not retrying"
                )
                return
            want_encode = self.get_encoding(stream_name)
            if want_encode != (mode == 'encode'):
                logger.info(
                    f"streamux {stream_name}: ffmpeg {mode} exited {rc}, "
                    f"encoding is now {want_encode}, not retrying"
                )
                return
            if mode == 'encode' and not want_encode:
                return
            if mode == 'encode' and self.get_profile(stream_name) != profile_id:
                logger.info(
                    f"streamux {stream_name}: ffmpeg {profile_id} exited {rc}, "
                    f"current profile is {self.get_profile(stream_name)}, not retrying"
                )
                return
            logger.warning(f"streamux {stream_name}: ffmpeg {mode} exited {rc}, retry in 3s")
            time.sleep(3)
            if int(gen) != self._current_gen(stream_name):
                logger.info(
                    f"streamux {stream_name}: generation moved during retry wait, "
                    f"not restarting {mode}"
                )
                return
            if self.get_encoding(stream_name) != (mode == 'encode'):
                logger.info(
                    f"streamux {stream_name}: encoding changed during retry wait, "
                    f"not restarting {mode}"
                )
                return
            with self._lock:
                replaced = self._procs.get(stream_name)
                self._stopping.discard(stream_name)
            if replaced is None:
                self._ensure(stream_name, gen=int(gen))
        else:
            with self._lock:
                self._stopping.discard(stream_name)

    def _log_tail(self, log_path: str, lines: int = 8) -> str:
        return '\n'.join(_tail_lines(log_path, lines)).strip()

    def _streamux_publish_pids(self, stream_name: str) -> list:
        """FFmpeg PIDs publishing the ATAK path — encode or passthrough, not {name}__src."""
        marker = f'publish:{stream_name}'.encode()
        src_marker = f'publish:{source_name(stream_name)}'.encode()
        pids = []
        try:
            entries = os.listdir('/proc')
        except OSError:
            return pids
        for entry in entries:
            if not entry.isdigit():
                continue
            try:
                with open(f'/proc/{entry}/cmdline', 'rb') as f:
                    cmd = f.read()
            except OSError:
                continue
            argv0 = cmd.split(b'\0', 1)[0]
            if b'ffmpeg' not in argv0:
                continue
            keep = False
            for part in cmd.split(b'\0'):
                if src_marker in part:
                    keep = False
                    break
                if marker in part:
                    keep = True
            if keep:
                pids.append(int(entry))
        return pids

    def _reap_orphans(self, stream_name: str, keep_pid: int | None = None):
        """SIGTERM leftover profile encoders the manager is not tracking."""
        for pid in self._streamux_publish_pids(stream_name):
            if keep_pid is not None and pid == keep_pid:
                continue
            try:
                os.kill(pid, signal.SIGTERM)
                logger.info(f"streamux {stream_name}: reaped orphan pid={pid}")
            except ProcessLookupError:
                pass
            except PermissionError as e:
                logger.warning(f"streamux {stream_name}: cannot reap pid={pid}: {e}")

    def _kill(self, stream_name: str):
        with self._lock:
            self._stopping.add(stream_name)
            proc = self._procs.pop(stream_name, None)
            stderr_file = self._stderr.pop(stream_name, None)
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
            except Exception as e:
                logger.error(f"streamux {stream_name}: kill error: {e}")
        self._reap_orphans(stream_name)
        if proc and proc.stdout:
            try:
                proc.stdout.close()
            except Exception:
                pass
        if stderr_file and hasattr(stderr_file, 'close'):
            try:
                stderr_file.close()
            except Exception:
                pass

    def _seed_overlay_text(self, stream_name: str, profile_id: str):
        fps = PROFILES.get(profile_id, {}).get('fps', 0)
        text = f'{_format_overlay_fps(str(fps))}  -- kbps'
        try:
            _atomic_write(_overlay_text_path(stream_name), text)
        except OSError as e:
            logger.warning(f"streamux {stream_name}: overlay text seed failed: {e}")

    def _read_progress(self, stream_name: str, proc: subprocess.Popen):
        if not proc.stdout:
            return
        path = _overlay_text_path(stream_name)
        buf: dict = {}
        try:
            for raw in proc.stdout:
                line = raw.decode('utf-8', errors='replace').strip()
                if not line or '=' not in line:
                    continue
                key, val = line.split('=', 1)
                buf[key] = val
                if key != 'progress':
                    continue
                text = (
                    f"{_format_overlay_fps(buf.get('fps', ''))}  "
                    f"{_format_overlay_bitrate(buf.get('bitrate', ''))}"
                )
                try:
                    _atomic_write(path, text)
                except OSError:
                    pass
                buf = {}
        except Exception as e:
            logger.debug(f"streamux {stream_name}: progress reader ended: {e}")

    def _build_cmd(self, stream_name: str, profile_id: str, overlay: bool = False,
                   font: str = '') -> list:
        from app.api.settings import server_settings
        spec = PROFILES[profile_id]
        transport = server_settings.get('rtsp_transport', 'tcp')
        src_url = f'{MEDIAMTX_RTSP_URL}/{source_name(stream_name)}'
        w, h, fps = spec['width'], spec['height'], spec['fps']
        # Wall-clock + setpts so ATAK/ffplay see 0,1,2… at this profile’s fps.
        # Source DTS (Frigate + setts=pts=DTS) otherwise leaks a huge PCR and
        # players show one frame every few seconds. Do not use +nobuffer here.
        vf = (
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
            f"fps={fps},setpts=N/{fps}/TB,format=yuv420p"
        )
        if overlay and font:
            text_path = _overlay_text_path(stream_name)
            fontsize = max(14, min(32, int(h / 16)))
            vf += (
                f",drawtext=fontfile={_ffmpeg_filter_path(font)}"
                f":fontsize={fontsize}:fontcolor=white"
                f":box=1:boxcolor=black@0.55:boxborderw=6"
                f":x=w-tw-12:y=h-th-12:textfile={_ffmpeg_filter_path(text_path)}:reload=1"
            )
        cmd = [
            'ffmpeg', '-loglevel', 'warning',
            '-rtsp_transport', transport,
            '-timeout', '8000000',
            '-fflags', '+discardcorrupt',
            '-use_wallclock_as_timestamps', '1',
            '-flags', 'low_delay',
            '-analyzeduration', '2000000',
            '-probesize', '2000000',
            '-i', src_url,
            '-map', '0:v:0',
            '-an',
            '-vf', vf,
            '-c:v', 'libx264',
            '-preset', 'ultrafast',
            '-tune', 'zerolatency',
            '-profile:v', spec['h264_profile'],
            '-level', spec['level'],
            '-pix_fmt', 'yuv420p',
            '-r', str(fps),
            '-fps_mode', 'cfr',
            '-b:v', spec['video_bitrate'],
            '-maxrate', spec['max_rate'],
            '-bufsize', spec['buf_size'],
            '-g', str(spec['gop']),
            '-keyint_min', str(spec['gop']),
            '-sc_threshold', '0',
            '-bf', '0',
            '-muxdelay', '0',
            '-muxpreload', '0',
            '-f', 'mpegts',
            f'srt://127.0.0.1:8890?streamid=publish:{stream_name}',
        ]
        if overlay:
            cmd[1:1] = ['-stats_period', '0.5']
            cmd[-1:-1] = ['-progress', 'pipe:1']
        return cmd

    def _build_passthrough_cmd(self, stream_name: str) -> list:
        """Cheap -c copy of {name}__src onto {name}. No x264. Ingest stays up."""
        from app.api.settings import server_settings
        transport = server_settings.get('rtsp_transport', 'tcp')
        src_url = f'{MEDIAMTX_RTSP_URL}/{source_name(stream_name)}'
        return [
            'ffmpeg', '-loglevel', 'warning',
            '-rtsp_transport', transport,
            '-timeout', '8000000',
            '-fflags', '+discardcorrupt',
            '-flags', 'low_delay',
            '-analyzeduration', '2000000',
            '-probesize', '2000000',
            '-i', src_url,
            '-map', '0:v:0',
            '-an',
            '-c', 'copy',
            '-muxdelay', '0',
            '-muxpreload', '0',
            '-f', 'mpegts',
            f'srt://127.0.0.1:8890?streamid=publish:{stream_name}',
        ]


streamux_manager = StreamuxManager()
