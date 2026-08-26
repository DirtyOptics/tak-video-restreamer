"""
StreamUx CM5 / box stats.

tvr-edge is Docker: /proc inside the container is a mix of kernel-global
counters (CPU, meminfo, uptime — typically the CM5) and a PID namespace
(process list = this container unless /host/proc is mounted).
CPU temp is /sys/class/thermal (usually cpu-thermal); no extra bind.

Do not invent host process tables from the container namespace.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import time

from app.config import DATA_DIR, STREAMS_DIR

logger = logging.getLogger(__name__)

PUBLISH_RE = re.compile(r'publish:([A-Za-z0-9_.-]+)')
TOP_N = 10
_HOST_PROC_DEFAULTS = ('/host/proc',)
_CPU_TEMP_TYPES = (
    'cpu-thermal',
    'cpu',
    'soc-thermal',
    'soc',
    'x86_pkg_temp',
    'k10temp',
    'coretemp',
)
_EMPTY_TEMP = {'celsius': None, 'type': None}


def _sysconf(name, default):
    try:
        val = os.sysconf(name)
        if val and val > 0:
            return int(val)
    except (ValueError, OSError, AttributeError):
        pass
    return default


def _read_text(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read()
    except OSError:
        return ''


def _read_bytes(path):
    try:
        with open(path, 'rb') as f:
            return f.read()
    except OSError:
        return b''


def format_uptime(seconds):
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return ''
    if s < 0:
        s = 0
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    parts = []
    if d:
        parts.append(f'{d}d')
    if h or d:
        parts.append(f'{h}h')
    if m or d or h:
        parts.append(f'{m}m')
    if not parts:
        parts.append(f'{s}s')
    return ' '.join(parts)


def format_bytes(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ''
    gb = n / (1024 ** 3)
    if gb >= 0.1:
        return f'{gb:.1f}GB'
    mb = n / (1024 ** 2)
    return f'{mb:.0f}MB'


def _parse_meminfo(text):
    kv = {}
    for line in text.splitlines():
        if ':' not in line:
            continue
        key, rest = line.split(':', 1)
        num = rest.strip().split()[0] if rest.strip() else ''
        try:
            kv[key] = int(num) * 1024  # kB → bytes
        except ValueError:
            continue
    total = kv.get('MemTotal')
    if not total:
        return None
    available = kv.get('MemAvailable')
    if available is None:
        available = (
            kv.get('MemFree', 0)
            + kv.get('Buffers', 0)
            + kv.get('Cached', 0)
        )
    used = max(0, total - available)
    return {
        'total_bytes': total,
        'used_bytes': used,
        'available_bytes': max(0, available),
        'percent': round(100.0 * used / total, 1) if total else None,
    }


def _parse_stat_cpu(text):
    total = idle = 0
    nproc = 0
    for line in text.splitlines():
        if line.startswith('cpu ') or line.startswith('cpu\t'):
            parts = line.split()
            if len(parts) < 5:
                continue
            nums = [int(x) for x in parts[1:]]
            total = sum(nums)
            idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
        elif line.startswith('cpu') and len(line) > 3 and line[3].isdigit():
            nproc += 1
    if total <= 0:
        return None
    return {'total': total, 'idle': idle, 'nproc': nproc or 1}


def _parse_loadavg(text):
    try:
        return float(text.split()[0])
    except (IndexError, ValueError):
        return None


def _parse_uptime(text):
    try:
        return float(text.split()[0])
    except (IndexError, ValueError):
        return None


def _parse_temp_milli(text):
    try:
        milli = int(text.strip().split()[0])
    except (IndexError, ValueError, AttributeError):
        return None
    if milli < -40000 or milli > 150000:
        return None
    return round(milli / 1000.0, 1)


def _temp_zone_score(ztype):
    t = (ztype or '').strip().lower()
    if t in _CPU_TEMP_TYPES:
        return 0
    if 'cpu' in t or t.startswith('soc'):
        return 1
    if 'gpu' in t or 'rp1' in t:
        return 9
    return 5


def _parse_pid_stat(text):
    """utime+stime clock ticks from /proc/pid/stat (comm may contain spaces)."""
    rparen = text.rfind(')')
    if rparen < 0:
        return None
    rest = text[rparen + 1:].split()
    if len(rest) < 13:
        return None
    try:
        return int(rest[11]) + int(rest[12])
    except (ValueError, IndexError):
        return None


def _parse_statm_rss_pages(text):
    parts = text.split()
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def _proc_label(comm, cmdline):
    comm = (comm or '').strip()
    cmd = cmdline.replace('\x00', ' ').strip()
    if comm.startswith('ffmpeg') or comm == 'ffmpeg.bin':
        m = PUBLISH_RE.search(cmd)
        if m:
            return f'ffmpeg {m.group(1)}'
        return 'ffmpeg'
    if 'mediamtx' in comm.lower() or 'mediamtx' in cmd.lower():
        return 'mediamtx'
    return comm or '?'


class HostStats:
    def __init__(
        self,
        proc_root='/proc',
        host_proc_candidates=None,
        disk_path=None,
        sys_root='/sys',
        clk_tck=None,
        page_size=None,
    ):
        self.proc_root = proc_root
        self.host_proc_candidates = host_proc_candidates
        self.disk_path = disk_path or DATA_DIR
        self.sys_root = sys_root
        self.clk_tck = clk_tck if clk_tck else _sysconf('SC_CLK_TCK', 100)
        self.page_size = page_size if page_size else _sysconf('SC_PAGESIZE', 4096)
        self._lock = threading.Lock()
        self._cpu_prev = None
        self._proc_prev = None

    def _candidates(self):
        extra = os.environ.get('STREAMUX_HOST_PROC', '').strip()
        listed = []
        if self.host_proc_candidates is not None:
            listed.extend(self.host_proc_candidates)
        else:
            if extra:
                listed.append(extra)
            listed.extend(_HOST_PROC_DEFAULTS)
        return listed

    def _looks_like_proc(self, root):
        if not root:
            return False
        return os.path.isfile(os.path.join(root, 'stat')) and os.path.isfile(
            os.path.join(root, 'uptime')
        )

    def _process_root(self):
        for cand in self._candidates():
            if self._looks_like_proc(cand):
                return cand, 'host'
        if self._looks_like_proc(self.proc_root):
            return self.proc_root, 'container'
        return None, 'unavailable'

    def _disk(self):
        for path in (self.disk_path, STREAMS_DIR, '/', os.getcwd()):
            if not path:
                continue
            try:
                usage = shutil.disk_usage(path)
            except OSError:
                continue
            total = int(usage.total)
            used = int(usage.used)
            if total <= 0:
                continue
            return {
                'total_bytes': total,
                'used_bytes': used,
                'free_bytes': int(usage.free),
                'percent': round(100.0 * used / total, 1),
                'path': path,
            }
        return None

    def _temp(self):
        base = os.path.join(self.sys_root, 'class', 'thermal')
        try:
            names = os.listdir(base)
        except OSError:
            return None
        zones = []
        for name in names:
            if not name.startswith('thermal_zone'):
                continue
            zdir = os.path.join(base, name)
            ztype = _read_text(os.path.join(zdir, 'type')).strip()
            celsius = _parse_temp_milli(_read_text(os.path.join(zdir, 'temp')))
            if celsius is None:
                continue
            zones.append((_temp_zone_score(ztype), name, ztype, celsius))
        if not zones:
            return None
        zones.sort(key=lambda z: (z[0], z[1]))
        _, _, ztype, celsius = zones[0]
        return {'celsius': celsius, 'type': ztype or None}

    def _list_pids(self, root):
        try:
            entries = os.listdir(root)
        except OSError:
            return []
        pids = []
        for name in entries:
            if name.isdigit():
                pids.append(name)
            if len(pids) >= 4096:
                break
        return pids

    def _read_processes(self, root):
        now = time.monotonic()
        rows = []
        for pid_s in self._list_pids(root):
            base = os.path.join(root, pid_s)
            cmdline = _read_bytes(os.path.join(base, 'cmdline'))
            if not cmdline:
                continue
            comm = _read_text(os.path.join(base, 'comm')).strip()
            ticks = _parse_pid_stat(_read_text(os.path.join(base, 'stat')))
            rss_pages = _parse_statm_rss_pages(_read_text(os.path.join(base, 'statm')))
            rss = (rss_pages * self.page_size) if rss_pages is not None else 0
            cmd = cmdline.decode('utf-8', 'replace')
            rows.append({
                'pid': int(pid_s),
                'name': _proc_label(comm, cmd),
                'ticks': ticks if ticks is not None else 0,
                'ram_bytes': rss,
                't': now,
            })
        return rows

    def _cpu_delta(self, sample):
        prev = self._cpu_prev
        self._cpu_prev = sample
        if not sample or not prev:
            return None
        dt = sample['total'] - prev['total']
        di = sample['idle'] - prev['idle']
        if dt <= 0:
            return None
        busy = max(0.0, min(100.0, 100.0 * (1.0 - (di / dt))))
        return round(busy, 1)

    def _proc_delta(self, rows, nproc):
        prev = self._proc_prev
        now_map = {}
        nproc = nproc or 1
        for r in rows:
            now_map[r['pid']] = r
            old = prev.get(r['pid']) if prev else None
            elapsed = r['t'] - old['t'] if old else 0
            if old and elapsed > 0 and self.clk_tck > 0:
                dticks = r['ticks'] - old['ticks']
                one = 100.0 * dticks / (self.clk_tck * elapsed)
                r['cpu_percent'] = round(max(0.0, one / nproc), 1)
            else:
                r['cpu_percent'] = None
        self._proc_prev = now_map
        return rows

    def snapshot(self, include_procs=False, wait_s=0.08):
        proc_root_stats = self.proc_root
        stats_ok = self._looks_like_proc(proc_root_stats)
        process_root, process_scope = self._process_root()
        host_mounted = process_scope == 'host'
        stats_scope = 'host_kernel' if stats_ok else 'unavailable'
        stats_label = 'CM5 host (kernel view)' if stats_ok else 'unavailable'
        if process_scope == 'host':
            proc_label = 'CM5 host'
        elif process_scope == 'container':
            proc_label = 'this container (tvr-edge)'
        else:
            proc_label = 'unavailable'

        def _cpu_sample():
            if not stats_ok:
                return None
            return _parse_stat_cpu(_read_text(os.path.join(proc_root_stats, 'stat')))

        cpu_sample = _cpu_sample()
        load1 = _parse_loadavg(_read_text(os.path.join(proc_root_stats, 'loadavg'))) if stats_ok else None
        up_s = _parse_uptime(_read_text(os.path.join(proc_root_stats, 'uptime'))) if stats_ok else None
        mem = _parse_meminfo(_read_text(os.path.join(proc_root_stats, 'meminfo'))) if stats_ok else None
        disk = self._disk()
        temp = self._temp()
        nproc = (cpu_sample or {}).get('nproc') or 1

        with self._lock:
            cpu_prime = cpu_sample is not None and self._cpu_prev is None
            proc_prime = include_procs and bool(process_root) and self._proc_prev is None
            if cpu_prime:
                self._cpu_prev = cpu_sample
            if proc_prime:
                self._proc_prev = {r['pid']: r for r in self._read_processes(process_root)}

        if wait_s and wait_s > 0 and (cpu_prime or proc_prime):
            time.sleep(wait_s)
            cpu_sample = _cpu_sample() or cpu_sample
            nproc = (cpu_sample or {}).get('nproc') or nproc

        with self._lock:
            cpu_pct = self._cpu_delta(cpu_sample) if cpu_sample else None
            nproc = (cpu_sample or {}).get('nproc') or nproc
            top_cpu = []
            top_ram = []
            if include_procs and process_root:
                rows = self._read_processes(process_root)
                rows = self._proc_delta(rows, nproc)
                total_ram = (mem or {}).get('total_bytes')
                for r in rows:
                    if total_ram:
                        r['ram_percent'] = round(100.0 * r['ram_bytes'] / total_ram, 1)
                    else:
                        r['ram_percent'] = None

                def _row(r):
                    return {
                        'pid': r['pid'],
                        'name': r['name'],
                        'cpu_percent': r.get('cpu_percent'),
                        'ram_percent': r.get('ram_percent'),
                        'ram_bytes': r['ram_bytes'],
                    }

                by_cpu = sorted(
                    rows,
                    key=lambda r: (r.get('cpu_percent') is not None, r.get('cpu_percent') or 0),
                    reverse=True,
                )
                by_ram = sorted(rows, key=lambda r: r.get('ram_bytes') or 0, reverse=True)
                top_cpu = [_row(r) for r in by_cpu[:TOP_N]]
                top_ram = [_row(r) for r in by_ram[:TOP_N]]

        cpu = {
            'percent': cpu_pct,
            'nproc': nproc if stats_ok else None,
            'load1': round(load1, 2) if load1 is not None else None,
        }
        uptime = {
            'seconds': int(up_s) if up_s is not None else None,
            'text': format_uptime(up_s) if up_s is not None else '',
        }
        return {
            'scope': {
                'stats': stats_scope,
                'stats_label': stats_label,
                'processes': process_scope,
                'processes_label': proc_label,
                'host_proc_mounted': host_mounted,
            },
            'cpu': cpu,
            'memory': mem or {
                'total_bytes': None,
                'used_bytes': None,
                'available_bytes': None,
                'percent': None,
            },
            'disk': disk or {
                'total_bytes': None,
                'used_bytes': None,
                'free_bytes': None,
                'percent': None,
                'path': None,
            },
            'temp': temp or dict(_EMPTY_TEMP),
            'uptime': uptime,
            'top_cpu': top_cpu if include_procs else [],
            'top_ram': top_ram if include_procs else [],
        }


host_stats = HostStats()


def read_hw(include_procs=False):
    try:
        return host_stats.snapshot(include_procs=include_procs)
    except Exception as e:
        logger.error(f'streamux hw: {e}')
        return {
            'scope': {
                'stats': 'unavailable',
                'stats_label': 'unavailable',
                'processes': 'unavailable',
                'processes_label': 'unavailable',
                'host_proc_mounted': False,
            },
            'cpu': {'percent': None, 'nproc': None, 'load1': None},
            'memory': {
                'total_bytes': None, 'used_bytes': None,
                'available_bytes': None, 'percent': None,
            },
            'disk': {
                'total_bytes': None, 'used_bytes': None,
                'free_bytes': None, 'percent': None, 'path': None,
            },
            'temp': dict(_EMPTY_TEMP),
            'uptime': {'seconds': None, 'text': ''},
            'top_cpu': [],
            'top_ram': [],
            'error': 'hw stats unavailable',
        }
