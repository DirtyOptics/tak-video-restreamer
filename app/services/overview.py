"""
Overview ladder — one published RTSP/SRT encode for ATAK.

Fat ingest stays on {name}__src (stream copy). The path ATAK pulls ({name})
is a single rung: low / medium / high. Switching restarts only the overview
encoder; the camera pull stays up.

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
DEFAULT_RUNG = 'high'
STATE_FILE = os.path.join(DATA_DIR, 'overview_rungs.json')
OVERLAY_STATE_FILE = os.path.join(DATA_DIR, 'overview_overlay.json')
OVERLAY_DIR = os.path.join(DATA_DIR, 'overview-overlay')
FFMPEG_LOG_DIR = os.path.join(LOGS_DIR, 'ffmpeg')
FONT_CANDIDATES = (
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
    '/usr/share/fonts/truetype/freefont/FreeSans.ttf',
    '/opt/app/fonts/DejaVuSans.ttf',
)

# Old ids from the first ladder; rewrite on load so state/API do not keep them.
RUNG_ALIASES = {
    'floor': 'low',
    'mid': 'medium',
    'g2g': 'high',
}

# ATAK Video wants H.264 on one RTSP/SRT URL. Low is 1 fps H.264, not MJPEG.
RUNGS = {
    'low': {
        'id': 'low',
        'label': 'Low',
        'detail': 'Always usable on a bad link',
        'budget': '1 fps · 426p · ~48 kbps H.264',
        'width': 426,
        'height': 240,
        'fps': 1,
        'video_bitrate': '48k',
        'max_rate': '64k',
        'buf_size': '96k',
        'gop': 1,
        'audio': False,
        'profile': 'baseline',
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
        'profile': 'baseline',
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
        'profile': 'main',
        'level': '4.0',
    },
}

mediamtx = MediaMTXClient(MEDIAMTX_API_URL)


def normalize_rung(rung: str) -> str:
    r = (rung or '').strip().lower()
    return RUNG_ALIASES.get(r, r)


def source_name(stream_name: str) -> str:
    return f'{stream_name}{SRC_SUFFIX}'


def is_source_path(path_name: str) -> bool:
    return bool(path_name) and path_name.endswith(SRC_SUFFIX)


def rung_catalog() -> list:
    return [
        {
            'id': r['id'],
            'label': r['label'],
            'detail': r['detail'],
            'budget': r['budget'],
        }
        for r in (RUNGS['low'], RUNGS['medium'], RUNGS['high'])
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


def _overlay_text_path(stream_name: str) -> str:
    safe = re.sub(r'[^A-Za-z0-9._-]+', '_', stream_name).strip('_') or 'stream'
    return os.path.join(OVERLAY_DIR, f'{safe}.txt')


def _atomic_write(path: str, text: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='ascii', errors='replace') as f:
        f.write(text)
    os.replace(tmp, path)


class OverviewManager:
    """One transcode process per public stream name."""

    def __init__(self):
        self._lock = threading.Lock()
        self._rungs: dict = {}
        self._overlays: dict = {}
        self._procs: dict = {}
        self._stderr: dict = {}
        self._errors: dict = {}
        self._stopping: set = set()
        self._load()
        self._load_overlays()

    def _load(self):
        try:
            if os.path.isfile(STATE_FILE):
                with open(STATE_FILE, 'r') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    migrated = {}
                    changed = False
                    for k, v in data.items():
                        nv = normalize_rung(v)
                        if nv in RUNGS:
                            migrated[k] = nv
                            if nv != v:
                                changed = True
                    self._rungs = migrated
                    if changed:
                        logger.info('overview: migrated rung ids to low/medium/high')
                        self._save()
        except Exception as e:
            logger.warning(f"overview: could not load {STATE_FILE}: {e}")

    def _save(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            tmp = STATE_FILE + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(self._rungs, f)
            os.replace(tmp, STATE_FILE)
        except Exception as e:
            logger.error(f"overview: could not save rungs: {e}")

    def _load_overlays(self):
        try:
            if os.path.isfile(OVERLAY_STATE_FILE):
                with open(OVERLAY_STATE_FILE, 'r') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._overlays = {
                        str(k): bool(v) for k, v in data.items()
                    }
        except Exception as e:
            logger.warning(f"overview: could not load {OVERLAY_STATE_FILE}: {e}")

    def _save_overlays(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            tmp = OVERLAY_STATE_FILE + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(self._overlays, f)
            os.replace(tmp, OVERLAY_STATE_FILE)
        except Exception as e:
            logger.error(f"overview: could not save overlay flags: {e}")

    def get_rung(self, stream_name: str) -> str:
        with self._lock:
            return self._rungs.get(stream_name, DEFAULT_RUNG)

    def get_overlay(self, stream_name: str) -> bool:
        with self._lock:
            return bool(self._overlays.get(stream_name, False))

    def status(self, stream_name: str) -> dict:
        rung = self.get_rung(stream_name)
        overlay = self.get_overlay(stream_name)
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
                last_error = 'ATAK path is down (404 / not ready). Ingest is still up. Restart the encoder.'
        return {
            'name': stream_name,
            'rung': rung,
            'overlay': overlay,
            'running': running,
            'sourcePath': src,
            'sourceReady': ingest,
            'publishedReady': published,
            'lastError': last_error,
        }

    def list_status(self, stream_names: list) -> list:
        return [self.status(n) for n in stream_names]

    def start(self, stream_name: str, wait: bool = False):
        """Ensure encoder is running. Non-blocking unless wait=True."""
        if wait:
            self._ensure(stream_name)
            return
        threading.Thread(
            target=self._ensure,
            args=(stream_name,),
            daemon=True,
            name=f'overview-{stream_name}',
        ).start()

    def set_rung(self, stream_name: str, rung: str, force: bool = False) -> dict:
        return self.update(stream_name, rung=rung, force=force)

    def set_overlay(self, stream_name: str, enabled: bool) -> dict:
        return self.update(stream_name, overlay=bool(enabled))

    def update(self, stream_name: str, rung: str | None = None, overlay: bool | None = None,
               force: bool = False) -> dict:
        if rung is not None:
            rung = normalize_rung(rung)
            if rung not in RUNGS:
                raise ValueError(f'Unknown rung: {rung}')
        pub = (mediamtx.get_path(stream_name) or {}).get('ready')
        with self._lock:
            current_rung = self._rungs.get(stream_name, DEFAULT_RUNG)
            current_overlay = bool(self._overlays.get(stream_name, False))
            new_rung = current_rung if rung is None else rung
            new_overlay = current_overlay if overlay is None else bool(overlay)
            proc = self._procs.get(stream_name)
            alive = proc and proc.poll() is None
            same = (
                (not force)
                and current_rung == new_rung
                and current_overlay == new_overlay
                and alive
                and pub
            )
            self._rungs[stream_name] = new_rung
            self._overlays[stream_name] = new_overlay
        self._save()
        self._save_overlays()
        if not same:
            logger.info(
                f"overview {stream_name}: rung {current_rung} -> {new_rung} "
                f"overlay {current_overlay} -> {new_overlay} force={force}"
            )
            self._restart(stream_name)
        from app.websocket.broadcast import broadcast
        st = self.status(stream_name)
        broadcast('overview_rung', st)
        return st

    def restart(self, stream_name: str) -> dict:
        return self.set_rung(stream_name, self.get_rung(stream_name), force=True)

    def stop(self, stream_name: str):
        self._kill(stream_name)
        src = source_name(stream_name)
        try:
            mediamtx.delete_path(src)
        except Exception:
            pass

    def _ensure(self, stream_name: str):
        if not self._wait_source(stream_name):
            logger.warning(f"overview {stream_name}: source not ready, encoder not started")
            return
        with self._lock:
            proc = self._procs.get(stream_name)
            keep_pid = proc.pid if proc and proc.poll() is None else None
        self._reap_orphans(stream_name, keep_pid=keep_pid)
        if keep_pid is not None:
            return
        self._spawn(stream_name)

    def _restart(self, stream_name: str):
        self._kill(stream_name)
        self._ensure(stream_name)

    def _wait_source(self, stream_name: str, timeout: float = 45.0) -> bool:
        src = source_name(stream_name)
        deadline = time.time() + timeout
        while time.time() < deadline:
            path = mediamtx.get_path(src) or {}
            if path.get('ready'):
                return True
            time.sleep(0.5)
        return False

    def _spawn(self, stream_name: str):
        rung_id = self.get_rung(stream_name)
        want_overlay = self.get_overlay(stream_name)
        font = find_font() if want_overlay else ''
        use_overlay = bool(want_overlay and font)
        font_error = ''
        if want_overlay and not font:
            font_error = (
                'Overlay is on, but no font is in the container. '
                'Install fonts-dejavu-core (or copy a TTF to /opt/app/fonts). '
                'Encoder is running without the overlay.'
            )
        if use_overlay:
            self._seed_overlay_text(stream_name, rung_id)
        cmd = self._build_cmd(stream_name, rung_id, overlay=use_overlay, font=font)
        os.makedirs(FFMPEG_LOG_DIR, exist_ok=True)
        log_path = os.path.join(FFMPEG_LOG_DIR, f'overview-{stream_name}.log')
        try:
            stderr_file = open(log_path, 'w')
        except OSError as e:
            logger.error(f"overview {stream_name}: cannot open log {log_path}: {e}")
            stderr_file = subprocess.DEVNULL

        mediamtx.add_path(stream_name, {
            'source': 'publisher',
            'overridePublisher': True,
        })

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
            logger.error(f"overview {stream_name}: ffmpeg start failed: {e}")
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
                name=f'overview-progress-{stream_name}',
            ).start()
        logger.info(
            f"overview {stream_name}: encoder started rung={rung_id} "
            f"overlay={use_overlay} pid={proc.pid}"
        )
        threading.Thread(
            target=self._watch,
            args=(stream_name, proc, rung_id),
            daemon=True,
            name=f'overview-watch-{stream_name}',
        ).start()

    def _watch(self, stream_name: str, proc: subprocess.Popen, rung_id: str):
        rc = proc.wait()
        with self._lock:
            fh = self._stderr.get(stream_name)
        if fh and hasattr(fh, 'flush'):
            try:
                fh.flush()
            except Exception:
                pass
        log_path = os.path.join(FFMPEG_LOG_DIR, f'overview-{stream_name}.log')
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
            if self.get_rung(stream_name) != rung_id:
                logger.info(
                    f"overview {stream_name}: ffmpeg {rung_id} exited {rc}, "
                    f"current rung is {self.get_rung(stream_name)}, not retrying"
                )
                return
            logger.warning(f"overview {stream_name}: ffmpeg exited {rc}, retry in 3s")
            time.sleep(3)
            with self._lock:
                replaced = self._procs.get(stream_name)
                self._stopping.discard(stream_name)
            if replaced is None:
                self._ensure(stream_name)
        else:
            with self._lock:
                self._stopping.discard(stream_name)

    def _log_tail(self, log_path: str, lines: int = 8) -> str:
        try:
            with open(log_path, 'r', errors='replace') as f:
                body = f.readlines()
            return ''.join(body[-lines:]).strip()
        except OSError:
            return ''

    def _overview_publish_pids(self, stream_name: str) -> list:
        """FFmpeg PIDs publishing the ATAK path — not the {name}__src ingest copy."""
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
            if b'libx264' not in cmd:
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
        """SIGTERM leftover rung encoders the manager is not tracking."""
        for pid in self._overview_publish_pids(stream_name):
            if keep_pid is not None and pid == keep_pid:
                continue
            try:
                os.kill(pid, signal.SIGTERM)
                logger.info(f"overview {stream_name}: reaped orphan pid={pid}")
            except ProcessLookupError:
                pass
            except PermissionError as e:
                logger.warning(f"overview {stream_name}: cannot reap pid={pid}: {e}")

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
                logger.error(f"overview {stream_name}: kill error: {e}")
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

    def _seed_overlay_text(self, stream_name: str, rung_id: str):
        fps = RUNGS.get(rung_id, {}).get('fps', 0)
        text = f'{_format_overlay_fps(str(fps))}  -- kbps'
        try:
            _atomic_write(_overlay_text_path(stream_name), text)
        except OSError as e:
            logger.warning(f"overview {stream_name}: overlay text seed failed: {e}")

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
            logger.debug(f"overview {stream_name}: progress reader ended: {e}")

    def _build_cmd(self, stream_name: str, rung_id: str, overlay: bool = False,
                   font: str = '') -> list:
        from app.api.settings import server_settings
        r = RUNGS[rung_id]
        transport = server_settings.get('rtsp_transport', 'tcp')
        src_url = f'{MEDIAMTX_RTSP_URL}/{source_name(stream_name)}'
        w, h, fps = r['width'], r['height'], r['fps']
        # Wall-clock + setpts so ATAK/ffplay see 0,1,2… at this rung’s fps.
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
                f":x=10:y=10:textfile={_ffmpeg_filter_path(text_path)}:reload=1"
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
            '-profile:v', r['profile'],
            '-level', r['level'],
            '-pix_fmt', 'yuv420p',
            '-r', str(fps),
            '-fps_mode', 'cfr',
            '-b:v', r['video_bitrate'],
            '-maxrate', r['max_rate'],
            '-bufsize', r['buf_size'],
            '-g', str(r['gop']),
            '-keyint_min', str(r['gop']),
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


overview_manager = OverviewManager()
