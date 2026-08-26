# TAK Video Restreamer — Feature Map

Complete inventory of every feature, API endpoint, service, and configuration option.
Intended as a porting reference.

**Fork / upstream (DirtyOptics):** product branch is `streamux-airbreach`; `main` tracks [raytheonbbn/tak-video-restreamer](https://github.com/raytheonbbn/tak-video-restreamer). Do not merge StreamUx into `main`. When pushing live work, commit onto `streamux-airbreach`. **Eventual** Raytheon updates: sync `main`, then merge `main` → `streamux-airbreach` and resolve glue (`streams.py`, `__init__` wiring, shared HTML/CSS) — StreamUx modules stay. Full operator path: [airbreach-lab/HANDOVER.md](../HANDOVER.md) gotcha **12**.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Protocol Support](#2-protocol-support)
3. [REST API — Streams](#3-rest-api--streams)
4. [REST API — Recording](#4-rest-api--recording)
5. [REST API — Recordings (Files)](#5-rest-api--recordings-files)
6. [REST API — Pull Streams](#6-rest-api--pull-streams)
7. [REST API — StreamUx](#7-rest-api--streamux)
8. [REST API — ABR / HLS Transcoding](#8-rest-api--abr--hls-transcoding)
9. [REST API — HLS File Serving](#9-rest-api--hls-file-serving)
10. [REST API — Test Patterns](#10-rest-api--test-patterns)
11. [REST API — Settings](#11-rest-api--settings)
12. [REST API — TLS / Certificates](#12-rest-api--tls--certificates)
13. [REST API — Authentication](#13-rest-api--authentication)
14. [REST API — Health & Status](#14-rest-api--health--status)
15. [REST API — Transcode & KLV Extraction](#15-rest-api--transcode--klv-extraction)
16. [WebSocket Events](#16-websocket-events)
17. [Web Pages (UI)](#17-web-pages-ui)
18. [Services](#18-services)
19. [Persistence Files](#19-persistence-files)
20. [Configuration (Environment Variables)](#20-configuration-environment-variables)
21. [Runtime Server Settings](#21-runtime-server-settings)
22. [Utility Modules](#22-utility-modules)
23. [External Process Management (FFmpeg)](#23-external-process-management-ffmpeg)
24. [Security Model](#24-security-model)

---

## 1. Architecture Overview

| Component | Technology | Purpose |
|---|---|---|
| Web server | Flask + Flask-SocketIO (eventlet) | REST API, WebSocket, static file serving |
| Media server | MediaMTX | RTSP/SRT/RTMP/WebRTC/HLS ingest & relay |
| Transcoding | FFmpeg | Recording, ABR HLS, pull streams, test patterns |
| ABR HLS | Flask serves segments from disk | Multi-bitrate adaptive streaming |
| Auth | Flask-Login + session cookies + API keys | Browser sessions and programmatic access |

**Key relationships:**
- MediaMTX accepts all publisher connections and relays streams to readers.
- Flask manages MediaMTX via its REST API (`/v3/...` on port 8889).
- Recording, ABR, and pull streams are FFmpeg child processes spawned by Flask.
- The browser UI polls `/api/streams` and connects via WebSocket for real-time updates.

---

## 2. Protocol Support

All protocols are handled by MediaMTX. Flask does not terminate these connections.

| Protocol | Port | Direction | Notes |
|---|---|---|---|
| RTSP (TCP) | 8554 | Push & Pull | Default transport |
| RTSP (UDP) | 8554 | Push & Pull | Optional transport |
| RTSPS (TLS) | 8555 | Push & Pull | Requires cert |
| SRT | 8890 | Push & Pull | `streamid=publish:<name>` / `read:<name>` |
| RTMP | 1935 | Push | |
| WebRTC | 8889 | Push & Pull | via HTTP |
| HLS (native MediaMTX) | 8888 | Pull (read) | Single bitrate, no ABR |
| HLS (ABR, Flask) | 3000 | Pull (read) | Multi-bitrate, served from `/hls/<name>/` |

---

## 3. REST API — Streams

All endpoints require authentication unless noted.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/streams` | List all streams from MediaMTX. Returns array of stream objects. Each includes `name`, `ready`, `numReaders`, `bytesReceived`, `bytesSent`, `recording`, `protocol`, `sourceAddress`, `sourceUrl`. Hides "phantom" streams that were explicitly deleted. |
| `GET` | `/api/streams/paths` | Returns a plain array of stream name strings. |
| `GET` | `/api/streams/<name>` | Get details for one stream. Adds `pulling`, `recordingInfo`, `pullInfo` when applicable. |
| `POST` | `/api/streams/<name>` | Create a persistent MediaMTX path config so the stream name survives publisher disconnect. Body: `{"source": "publisher"}`. |
| `POST` | `/api/streams/<name>/stop` | Disable ingest: stop pull FFmpeg, encoder, recording, kick clients, remove MediaMTX paths. Keeps the pull config (in memory and `pull_sources.json` with `stopped: true`) so Dashboard/StreamUx still show a stopped card. |
| `POST` | `/api/streams/<name>/start` | Resume a stopped pull from the saved URL. Re-creates MediaMTX paths and starts FFmpeg + encoder. |
| `DELETE` | `/api/streams/<name>` | Full delete: stop all components, kick connections, delete MediaMTX path config, remove pull persistence, add to hidden set so phantom path is suppressed. |

**Stream object fields:**
```json
{
  "name": "drone1",
  "ready": true,
  "numReaders": 2,
  "bytesReceived": 12345678,
  "bytesSent": 9876543,
  "recording": false,
  "protocol": "SRT",
  "sourceAddress": "192.168.1.50",
  "sourceUrl": "rtsp://192.168.1.50:8554/live",
  "lastDataTime": "2025-12-09T21:34:33+00:00"
}
```

**Hidden streams:** When a stream is deleted via `DELETE`, it is added to an in-memory `hidden_streams` set. The list endpoint suppresses it even if MediaMTX still shows a regex-matched phantom path. It is automatically un-hidden when the path becomes `ready` again.

**Source protocol detection:** Flask calls MediaMTX connection-list endpoints (`/v3/srtconns/list`, `/v3/rtspsessions/list`, etc.) to resolve the remote IP and protocol for each stream's `source` field.

---

## 4. REST API — Recording

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/streams/<name>/record` | Start recording. Auto-detects codec via ffprobe on RTSP first, then SRT fallback. Re-encodes to H.264 CFR 29.97 fps with drop-frame timecode. Preserves KLV data streams if detected. Output: `.mov` in `STREAMS_DIR/<name>/`. Body: `{reencode: bool, copyMode: bool}`. `copyMode` forces stream-copy (no re-encode) for fastest recording. |
| `POST` | `/api/streams/<name>/stop-record` | Stop recording gracefully (sends `q` to FFmpeg stdin, waits up to 10s, then terminates). Runs post-stop analysis and queues post-processing. |
| `GET` | `/api/streams/<name>/recording-status` | Status for one stream: `recording`, `startTime`, `duration`, `outputFile`, `codec`. |
| `GET` | `/api/recording-status` | Status for all currently recording streams. Returns `{stream_name: {...}}` dict. |
| `GET` | `/api/post-processing/status` | Status of the post-processing queue: `isProcessing`, `queueLength`, queue items. |

**Recording FFmpeg command details:**
- Input: `rtsp://localhost:8554/<name>` (TCP) or `srt://localhost:8890?streamid=read:<name>`
- Flags: `-err_detect ignore_err`, `-fflags +genpts+discardcorrupt`, `-flags low_delay`
- Video: `libx264 ultrafast`, CRF 23, force 29.97 fps CFR, `vsync cfr`
- Audio: `aac`, 128 kbps
- Data: `-c:d copy` if KLV detected
- Container: MOV with `-movflags +faststart+write_colr`
- Metadata embedded: `title`, `date`, `creation_time` (UTC ISO 8601), `timecode`, `comment`, `artist`, `encoder`

**Auto-record:** When `auto_record_enabled` is `true` in `app.state`, new streams that become ready are automatically started recording. Controlled via the settings API.

---

## 5. REST API — Recordings (Files)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/recordings` | List all `.mp4/.mov/.ts/.mxf/.mpg/.mpeg/.mkv` files under `STREAMS_DIR`. Returns `stream`, `filename`, `size`, `created`, `modified`, `hasThumbnail`, `url`, `thumbnailUrl`, `status` (`recording` or `finalized`). Sorted newest-first. Skips `_backup`, `_temp`, `test_tmcd` files. |
| `GET` | `/api/recordings/<stream>/<filename>` | Download a recording file (as-attachment). Validates filename and path traversal. |
| `DELETE` | `/api/recordings/<stream>/<filename>` | Delete recording + its `_thumb.jpg` if present. |
| `POST` | `/api/recordings/bulk-delete` | Delete multiple recordings. Body: `{"recordings": [{"stream": "...", "filename": "..."}]}`. Returns `deletedCount`, `totalRequested`, `errors`. |
| `GET` | `/api/recordings/<stream>/<filename>/thumbnail` | Serve thumbnail JPEG (no-cache headers). |
| `POST` | `/api/recordings/<stream>/<filename>/generate-thumbnail` | Generate thumbnail from video via FFmpeg. Stores as `<basename>_thumb.jpg`. |
| `GET` | `/api/recordings/<stream>/<filename>/metadata` | Run ffprobe and return: `duration`, `size`, `codec`, `resolution`, `fps`, `bitrate`, `audio_codec`, `tags`, `keywords`. Keywords extracted from `comment` tag (delimiter ` | Keywords: `). |
| `POST` | `/api/recordings/<stream>/<filename>/keywords` | Embed keywords into video file metadata (FFmpeg stream-copy, no re-encode). Preserves all other metadata. Uses temp-file-then-move pattern. Strips pipe `|` from keywords. |

**Security on file endpoints:**
- Stream name validated against `^[a-zA-Z0-9_-]+$`, max 64 chars.
- Filename validated for path traversal (`..`, `/`, `\`) and allowed extensions.
- Resolved path checked to be within `STREAMS_DIR`.

---

## 6. REST API — Pull Streams

A pull stream re-publishes an external RTSP/SRT source into MediaMTX via FFmpeg.

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/streams/<name>/pull` | Start a pull stream. Body: `{"url": "rtsp://...", "username": "", "password": ""}`. Saves config to `pull_sources.json`. Starts FFmpeg and a monitor thread. |
| `POST` | `/api/streams/<name>/stop-pull` | Stop pull stream gracefully, disable auto-retry, remove from `pull_sources.json`. |
| `GET` | `/api/streams/<name>/pull-status` | Status for one pull stream: `active`, `sourceUrl`, `retryCount`, `autoRetry`, `uptime`. |
| `GET` | `/api/pull-status` | Status for all pull streams. |
| `DELETE` | `/api/streams/<name>/standby` | Remove stream from standby tracking. |

**Pull stream FFmpeg command:**
```
ffmpeg -rtsp_transport tcp -buffer_size <N> -max_delay <N>
       -analyzeduration 2000000 -probesize 2000000
       -i <source_url>
       -map 0 -err_detect ignore_err
       -fflags +genpts+discardcorrupt+nobuffer
       -flags low_delay -c copy
       -f rtsp -rtsp_transport tcp
       rtsp://localhost:8554/<stream_name>
```

**Auto-retry behavior (configurable via Settings UI):**
- `auto_reconnect` (default: true) — enable/disable automatic retry
- `max_reconnect_attempts` (default: -1, infinite)
- `reconnect_delay` (default: 5s) — base delay between retries
- `exponential_backoff` (default: false) — doubles delay each attempt up to `max_backoff_delay`
- On disconnect, finalizes any active recording, waits, restarts FFmpeg
- Broadcasts `pull_stream_retrying`, `pull_stream_reconnected`, `pull_stream_failed`
- Config survives container restart via `pull_sources.json` (live pulls auto-restored 10s after startup; `stopped: true` entries stay listed but do not auto-start)

---

## 7. REST API — StreamUx

StreamUx is **not** ABR HLS. Fat ingest stays on `{name}__src` (stream copy). One published RTSP/SRT path on `{name}` for ATAK. **Encoding on** (default): Profiles **Low / Medium / High** (x264). **Encoding off:** cheap `-c copy` passthrough of `{name}__src` onto `{name}` so existing viewers keep the picture; ingest stays up; does **not** call Dashboard Stop. Hard cutover 2026-08-26: `/api/overview` is gone; JSON uses `profiles` / `profile` (no `rungs` / `rung` aliases).

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/streamux` | Catalog + per-pull status. `{profiles: [...], streams: [...]}`. |
| `GET` | `/api/streamux/hw` | Box stats for the StreamUx page. `{scope, cpu, memory, disk, temp, uptime}`. `scope.stats` is `host_kernel` (CPU/meminfo/uptime from `/proc` — kernel-global) or `unavailable`. `temp` is `{celsius, type}` from `/sys/class/thermal` (prefers `cpu-thermal`; `celsius` null if no zone). `scope.processes` is `container` (this `tvr-edge` PID namespace) unless `/host/proc` (or `STREAMUX_HOST_PROC`) is mounted, then `host`. Query `?procs=1` adds `top_cpu` / `top_ram` (name, cpu_percent of whole box, ram_percent, ram_bytes, pid). ffmpeg names include `publish:<pull>` only — no source URLs. Auth same as other `/api/streamux` routes. |
| `GET` | `/api/streamux/<name>` | Status for one pull: `name`, `profile` (`low`/`medium`/`high`), `overlay`, `encoding` (bool, missing persist = on), `mode` (`encode` / `passthrough`), `running`, `sourcePath`, `sourceReady`, `publishedReady`, `lastError`, `sourceUrl`, `stopped`. `lastError` is always included (empty string if none). UI shows it on the card when set, including when Streaming is off / stuck. |
| `GET` | `/api/streamux/<name>/log` | Encoder log tail. `{name, lastError, lines: ["..."]}`. Query `lines` default 100, max 100. Reads from EOF (`streamux-<name>.log`, else legacy `overview-<name>.log`). 404 if not a known pull. Empty `lines` if no file yet. On-demand (and while an open panel has Auto-scroll); not part of the main status poll. |
| `PUT` | `/api/streamux/<name>` | Change profile and/or overlay and/or encoding. Body must include `profile` and/or `overlay` and/or `encoding`. `profile` required **not** `rung`. Overlay-only and encoding-only are allowed. Encoding on → x264 profile encode; off → passthrough copy (ingest stays up, published path stays, no MTX reader kick / no `_stop_stream_components`). Profile PUT while encoding is off returns **409** (`Turn encoding on to change profile`) unless the same body also sets `encoding: true`. Encoder/passthrough ffmpeg restarts when the live mode actually changes. 409 if the pull is stopped. |
| `POST` | `/api/streamux/restart` | Force-restart the **profile encoder**. Body: `{"name": "<stream>"}`. **409** if encoding is off. |

**Profile catalog (`profiles`):** `id` `low` / `medium` / `high`, plus `label`, `detail`, `budget`. Default for new pulls is **Medium** (`DEFAULT_PROFILE`) with **encoding on**. On-disk ids `floor`/`mid`/`g2g` still rewrite to `low`/`medium`/`high` (profile-id aliases, not API field names). Missing `encoding` key = **on**.

**Encoding control (StreamUx card):** compact `.st` pill in `.streamux-meta` next to ffmpeg / ingest / Streaming (CSS cache-bust `v=streamux-overlay-lock-20260826`). Off = no x264; ingest/pull stays up; published RTSP/SRT is a cheap copy of `{name}__src`. Profile buttons disabled with hint “Turn encoding on to change profile”. Overlay checkbox is **disabled** while encoding is off (persist flag stays; it applies again when encoding is on — `-c copy` cannot burn overlay). Encoding off shows **passthrough** (not Dashboard Stopped). `streamux_manager.start` (Dashboard pull start / restore / retry) spawns passthrough instead of x264 when encoding is off.

**WebSocket:** `streamux_profile` with the same status object as GET.

**Persistence:** `streamux_profiles.json` is `{stream_name: {profile, encoding}}` (legacy string values migrate; missing encoding = on), `streamux_overlay.json`, overlay dir `streamux-overlay/`. One-shot migrate from `overview_rungs.json` / `overview_overlay.json` / `overview-overlay` if the new path is missing. Encoder logs `streamux-{name}.log` (new processes only; passthrough spawns append too).

**Client URLs (unchanged):** `rtsp://<host>:8554/<name>` · `srt://<host>:8890?streamid=read:<name>`.

---

## 8. REST API — ABR / HLS Transcoding

ABR = Adaptive Bitrate. Multi-rendition FFmpeg → HLS segments on disk. Served by Flask at `/hls/`.

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/streams/<name>/abr` | Enable ABR for a stream. Returns `{status: "started" | "already_running"}`. Persists to `abr_state.json`. |
| `DELETE` | `/api/streams/<name>/abr` | Disable ABR for a stream. Stops FFmpeg process (or orphans if unkillable). Cleans HLS output dir. |
| `GET` | `/api/streams/<name>/abr/status` | `{running, stream_name, state, pid, renditions, uptime_seconds}` |
| `GET` | `/api/abr` | List all active ABR processes. |

**ABR rendition tiers:**

| Tier | Default resolution | Default video bitrate | Default audio |
|---|---|---|---|
| high  | 1280×720  | 2500 kbps | 128 kbps AAC |
| medium (optional) | 960×540 | 1200 kbps | 96 kbps AAC |
| low   | 640×360   | 600 kbps  | 64 kbps AAC |

**ABR FFmpeg command structure:**
- Input: `rtsp://127.0.0.1:8554/<stream_name>` (TCP)
- One `-map 0:v -map 0:a?` per rendition with `libx264`, resolution scaling, CBR-max, profile/level
- Output: HLS segments to `HLS_OUTPUT_DIR/<stream_name>/v<variant>/index.m3u8`
- Master playlist: `HLS_OUTPUT_DIR/<stream_name>/master.m3u8`
- HLS options: `hls_time=4`, `hls_list_size=10`, `hls_flags=delete_segments+independent_segments`

**Stall detection:**
- Background thread checks segment mtimes every 10s
- If no update for 30s after a 15s startup grace: orphan FFmpeg, start new process
- "Orphan" = no blocking wait; stale process may still be in D-state (kernel-level I/O wait)

**State persistence:**
- `abr_state.json` stores which streams had ABR enabled
- On startup, waits 15s then polls each stream until `ready=true` before restarting ABR
- Settings stored in `abr_settings.json`; `abr_manager.apply_settings()` restarts running ABR transcodes

---

## 9. REST API — HLS File Serving

Served at `PORT` (3000) by Flask, not by MediaMTX.

| Method | Path | Description |
|---|---|---|
| `GET` | `/hls/<name>/master.m3u8` | ABR master playlist. Returns 503 (with `Retry-After: 5`) if ABR is not running for that stream, or if the playlist file is stale beyond `HLS_SEGMENT_DURATION * (HLS_LIST_SIZE + 2)` seconds. |
| `GET` | `/hls/<name>/v<variant>/index.m3u8` | Variant-level playlist. Same 503 logic. |
| `GET` | `/hls/<name>/v<variant>/<segment>.ts` | HLS segment (binary). Served with `Cache-Control: public, max-age=60`. |
| `GET` | `/hls/<name>/player` | Returns an hls.js HTML player page. Auto-detects ABR vs native HLS: uses ABR master playlist when ABR is running, falls back to MediaMTX native HLS via CORS proxy when not. Shows source mode label ("ABR mode" / "Native mode"). |
| `GET/HEAD` | `/api/hls/proxy/<name>/<path:filename>` | Proxy MediaMTX native HLS with CORS headers for cross-origin embedding. Optional `?videoonly=1` strips audio renditions from M3U8 playlists. |

**CORS:** All `/hls/` responses include `Access-Control-Allow-Origin`, `-Methods`, `-Headers`, `-Expose-Headers`, `-Max-Age`. Allows external sites to embed streams.

**Player page features:**
- hls.js self-hosted (offline-capable)
- Automatic source detection: ABR master playlist when ABR is active, MediaMTX native HLS (via CORS proxy) when ABR is not running
- Source mode label ("ABR mode" / "Native mode")
- Quality selector dropdown (populated from manifest parsed event; hidden when only one rendition or in native mode)
- Current quality info display (resolution + bitrate)
- Error recovery: network errors → `hls.startLoad()`; media errors → `hls.recoverMediaError()` up to 3×, then destroy + recreate after 3s; other fatal → destroy + recreate after 3s
- `lang="en"` attribute prevents browser auto-translate prompts

---

## 10. REST API — Test Patterns

Generates synthetic video+audio via FFmpeg lavfi and publishes to MediaMTX.

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/test/srt` | Publish test pattern via SRT. Body: `{streamName, resolution, duration, pattern, framerate}`. Defaults: 1280×720, 60s, testsrc, 30fps. |
| `POST` | `/api/test/rtsp` | Publish via RTSP TCP. Same options. |
| `POST` | `/api/test/rtsps` | Publish via RTSPS (encrypted RTSP). Same options. |
| `POST` | `/api/test/rtsp-udp` | Publish via RTSP UDP. Same options. |
| `GET` | `/api/test/list` | List all active test patterns with elapsed time. Used by test page for restore-on-refresh. |
| `POST` | `/api/test/<test_id>/stop` | Stop a running test publisher. |
| `GET` | `/api/test/<test_id>/status` | Returns `running`, `status`, `protocol`, `streamName`, `resolution`, `duration`, `pattern`, `framerate`, `elapsed`. |

**Parameters (all POST endpoints):**
- `streamName` (required): Target stream name
- `resolution` (optional, default `"1280x720"`): e.g. `"1920x1080"`
- `duration` (optional, default `60`): Seconds. `0` = continuous. Clamped to 5–3600.
- `pattern` (optional, default `"testsrc"`): `testsrc`, `testsrc2`, `smptebars`, `smptehdbars`, `color`
- `framerate` (optional, default `30`): `24`, `25`, `29.97`, `30`, `60`

All test publishers are tracked in `active_tests` dict and are stopped when the parent stream is stopped or deleted.

---

## 11. REST API — Settings

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/status` | Quick system status: `status`, `timestamp`, `activeStreams`, `activeRecordings`, `klvAvailable`, `activePullStreams`, `autoRecordEnabled`. |
| `GET` | `/api/settings` | All runtime settings + `disk` usage info (total/used/free GB, percent, device, path) + `autoRecord`, `pullStreamBufferSize`, `pullStreamMaxDelay`. |
| `POST` | `/api/settings` | Update one or more runtime settings. Type-validated. Returns `updated` diff, `warnings` for non-fatal errors. Broadcasts `settings_updated`. |
| `GET` | `/api/auto-record-status` | `{enabled: bool}` |
| `POST` | `/api/auto-record-toggle` | Body: `{enabled: bool}`. Broadcasts `auto_record_changed`. |
| `GET` | `/api/settings/auto-record` | Same as above GET. |
| `POST` | `/api/settings/auto-record` | Same as above POST. |
| `GET` | `/api/settings/srt` | SRT URL parameter settings (port, latency, maxbw, pbkeylen, passphrase, transtype, etc.). |
| `POST` | `/api/settings/srt` | Update SRT settings. Validates latency (20–8000), pbkeylen (0/16/24/32), passphrase length (10–79). Returns example URL. |
| `GET` | `/api/settings/abr` | ABR rendition settings + dropdown option lists. |
| `POST` | `/api/settings/abr` | Update ABR settings. Validates resolutions/bitrates against allowed lists. Calls `abr_manager.apply_settings()` to restart running processes. |
| `POST` | `/api/streams/<name>/buffer` | Enable/disable SRT buffering for a stream. Body: `{enable: bool}`. Requires optional `srt_buffer` module. |
| `GET` | `/api/settings/certificates/status` | TLS cert info: `installed`, `cert_exists`, `key_exists`, `subject`, `issuer`, `expires`. Uses `openssl x509`. |
| `POST` | `/api/settings/certificates/upload` | Upload `.crt`/`.pem`/`.cer` + `.key`/`.pem`. Validates with `openssl x509 -noout`. Backs up existing certs with timestamp suffix. |
| `POST` | `/api/settings/certificates/generate` | Generate 4096-bit RSA self-signed cert via `openssl req -x509`, 10-year validity, CN=localhost. |

---

## 12. REST API — TLS / Certificates

Separate blueprint (`tls_api.py`) using `app.services.tls`.

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/tls/settings` | ✓ | TLS settings + cert status. |
| `POST` | `/api/tls/settings` | ✓ | Update TLS settings (`rtsps_enabled`, `https_enabled`, etc.). Audit-logged. |
| `POST` | `/api/tls/self-signed` | ✓ | Generate self-signed cert. Body: `{common_name}`. Audit-logged. |
| `POST` | `/api/tls/letsencrypt` | ✓ | Request Let's Encrypt cert. Body: `{domain, email}`. Audit-logged. |
| `POST` | `/api/tls/renew` | ✓ | Renew Let's Encrypt cert. Audit-logged. |
| `POST` | `/api/tls/upload` | ✓ | Upload cert + key files. Validates extensions. Stores as `server.crt` / `server.key`. Audit-logged. |
| `GET` | `/api/tls/cert-status` | ✓ | Current certificate details. |

---

## 13. REST API — Authentication

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/auth/login` | Public | Session login. Body: `{username, password}`. Returns `{success, redirect, default_password}`. Audit-logged. |
| `POST` | `/api/auth/logout` | ✓ | End session. Audit-logged. |
| `GET` | `/api/auth/status` | Public | `{authenticated, default_password, username}`. |
| `GET` | `/api/auth/me` | ✓ | `{username, default_password}`. |
| `GET` | `/api/auth/keys` | ✓ | List API keys (hash, name, created — never raw key). |
| `POST` | `/api/auth/keys` | ✓ | Generate API key. Body: `{name}`. Returns raw key **once**. Audit-logged. |
| `DELETE` | `/api/auth/keys/<hash>` | ✓ | Revoke API key. Audit-logged. |
| `GET` | `/api/audit` | ✓ | Recent audit log entries. Query: `?lines=200`. |

**Auth mechanisms:**
- Browser: session cookie (Flask-Login, `remember=True`)
- API clients: `X-API-Key` header (hashed and compared; raw key never stored)
- Default credentials: `admin` / `changeme` — `default_password` flag returned on login/status to prompt UI warning
- All write endpoints protected by `@auth_required` decorator

---

## 14. REST API — Health & Status

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | Public | `{status, timestamp, systemStartTime, mediamtx, klvAvailable, srtBufferAvailable, activeRecordings, activePullStreams, pullStreamConfigs, autoRecordEnabled}`. `status` returns `"healthy"` or `"degraded"` (when MediaMTX is unreachable). `mediamtx` returns `"up"` or `"down"`. |
| `GET` | `/api/status` | Public | Simplified status for client compatibility. |

---

## 15. REST API — Transcode & KLV Extraction

File transcoding and KLV metadata extraction endpoints in `app.api.utils`.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/transcode/options` | Available transcode formats: (1) MOV with corrected timecode, (2) MP4 + KLV backup, (3) MXF + KLV backup, (4) MPEG-TS with embedded KLV (recommended default). |
| `POST` | `/api/transcode` | Start a transcode job. Body: `{inputFile, option (1-4), streamName}`. Options 2–4 require the KLV module. Returns `transcode_id`. |
| `GET` | `/api/transcode/<id>/status` | Status of a specific transcode job: `status`, `progress`, `elapsed`, `outputFile`, `pid`. |
| `GET` | `/api/transcode/status` | Status of all transcode jobs. |
| `DELETE` | `/api/transcode/<id>` | Cancel a running transcode job (SIGTERM to FFmpeg process). |
| `POST` | `/api/klv/extract` | Extract KLV metadata from a recording. Body: `{videoFile, includeRaw}`. `videoFile` is relative to `STREAMS_DIR`. Requires KLV module. |

**Transcode output formats:**

| Option | Format | Container | KLV handling |
|---|---|---|---|
| 1 | MOV with corrected timecode | QuickTime MOV | None |
| 2 | MP4 + KLV backup | MP4 | Separate binary KLV file |
| 3 | MXF + KLV backup | Broadcast MXF (MPEG-2) | Separate binary KLV file |
| 4 | MPEG-TS with embedded KLV | MPEG Transport Stream (H.264) | STANAG 4609 embedded |

**WebSocket events:** `transcode_complete` (on success or failure, with `success` flag), `transcode_cancelled`.

---

## 16. WebSocket Events

Server-sent events (Flask-SocketIO). Clients connect to the same port as the web UI (3000).

| Event name | Payload | Trigger |
|---|---|---|
| `connected` | `{timestamp}` | On client WebSocket connect |
| `stream_created` | `{name}` | Stream path registered in MediaMTX |
| `stream_stopped` | `{name, connections, components}` | Stream stop requested |
| `stream_deleted` | `{name, components, connections}` | Stream deleted |
| `recording_started` | `{name, file, timecode, hasKlv}` | FFmpeg recording process started |
| `recording_stopped` | `{name, file, duration, analysis}` | FFmpeg process exited |
| `pull_stream_started` | `{name, source}` | Pull FFmpeg process started |
| `pull_stream_stopped` | `{name, code}` | Pull ended with no retry |
| `pull_stream_retrying` | `{name, code, retry_count, retry_delay}` | Pull disconnected, queuing retry |
| `pull_stream_reconnected` | `{name, source, retry_count}` | Pull retry succeeded |
| `pull_stream_failed` | `{name, reason}` | Max retries exceeded |
| `auto_record_changed` | `{enabled}` | Auto-record setting toggled |
| `settings_updated` | `{settings}` | Runtime settings changed |
| `abr_settings_updated` | `{settings}` | ABR rendition settings changed |
| `pull_stream` | `{name, active, sourceUrl}` | Pull stream status update |
| `stream_stopped` | `{name}` | Stream explicitly stopped |
| `codec_detecting` | `{stream, file}` | Codec detection started for a recording |
| `stream_standby_update` | list of standby records | Stream goes active or to standby |
| `transcode_complete` | `{id, inputFile, option, success, outputFile?, duration?, error?}` | Transcode finished (success or failure indicated by `success` flag) |
| `transcode_cancelled` | `{id}` | Transcode job cancelled |
| `streamux_profile` | `{name, profile, overlay, running, sourcePath, sourceReady, publishedReady, lastError}` | StreamUx profile or overlay changed / encoder restarted |

---

## 17. Web Pages (UI)

All pages require authentication (redirects to `/login`). All share the same nav header.

| Route | File | Description |
|---|---|---|
| `/login` | `login.html` | Login form. Warns if default password is in use. |
| `/` | `index.html` | **Dashboard.** Lists active streams with cards. Per-stream: protocol/source info, bytes in/out, reader count, Start/Stop Pull, Record, ABR toggle, Delete, Stream Details modal. Displays system status (uptime, disk, active recordings, pull streams). |
| `/streamux` | `overview.html` | **StreamUx.** Hardware Monitor (CPU / Memory / Disk / Temp / Uptime + collapsible What’s using CPU/RAM?) between the intro and **Profiles**. Profiles Low / Medium / High for each pull. Overlay fps/bitrate. Status pills (ffmpeg / ingest / Streaming). RTSP `:8554` and SRT `:8890` client URLs. Not ABR HLS. |
| `/recordings` | `recordings.html` | Browse all recording files. Filter by stream, sort by date/size/name. Thumbnail previews. Download, delete, bulk-delete. View/edit keywords. View metadata. |
| `/settings` | `settings.html` | Runtime settings editor (recording, reconnect, SRT, ABR renditions, standby). Auto-record toggle. ABR global settings. SRT URL parameter builder. TLS certificate management. |
| `/utils` | `utils.html` | Utility tools. Two tabs: **Transcode Video** (select recording + transcode option, progress bar) and **Extract KLV** (extract STANAG 4609 metadata from recordings). |
| `/test` | `test_video_input.html` | Test Video Input page. Launch test pattern publishers (SRT, RTSP TCP, RTSPS, RTSP UDP) against any stream name. Pattern selector (testsrc, testsrc2, smptebars, smptehdbars, color). Duration 0 = continuous mode (max 3600s). Live elapsed timer. Restore-on-refresh via `GET /api/test/list`. |
| `/videowall` | `videowall.html` | Video Wall. 2×2 or 3×3 grid of hls.js players. Per-cell stream selector. Layout and cell assignments persisted in `localStorage`. One-at-a-time audio unmute. |

**Stream Details modal** (on Dashboard):
- **From:** pull stream source URL (copyable) or push protocol + source IP
- **To:** protocol-matched ingest URL for this stream
- **Watch:** RTSP read, SRT read, HLS (port 8888), HLS ABR (port 3000), HLS Player (link)
- All URLs have Copy buttons

---

## 18. Services

### `app.services.mediamtx.MediaMTXClient`

Thin wrapper around MediaMTX REST API (v3).

| Method | MediaMTX call | Purpose |
|---|---|---|
| `list_paths()` | `GET /v3/paths/list/` | All active stream paths |
| `get_path(name)` | `GET /v3/paths/get/<name>` | Single path details |
| `add_path(name, config)` | `POST /v3/config/paths/add/<name>` then fallback `PATCH /v3/config/paths/patch/<name>` | Upsert persistent path config |
| `delete_path(name)` | `DELETE /v3/config/paths/delete/<name>` | Remove path config |
| `list_connections()` | `GET /v3/srtconns/list`, `rtspsessions/list`, `rtmpconns/list` | All active connections across protocols |
| `kick_connection(type, id)` | `POST /v3/<type>/kick/<id>` | Force-disconnect a client |

### `app.services.abr.ABRManager`

Singleton. Manages one FFmpeg process per stream for ABR HLS.

- `start(stream_name)` — build FFmpeg args, spawn process, start monitor+stall-check threads, persist state
- `stop(stream_name)` — terminate process (no blocking wait), clean HLS dir, remove from state file
- `status(stream_name)` — running state, PID, uptime, rendition list
- `list_active()` — all running streams
- `restore_state()` — on startup, re-enables ABR for saved streams after 15s
- `apply_settings(settings)` — reload renditions, restart all running ABR processes
- `_wait_for_source_and_start(name)` — polls MediaMTX path readiness (up to 10 × 5s)

### `app.services.streamux.StreamuxManager`

Singleton. One published ffmpeg per pull stream (ATAK path `{name}`; ingest `{name}__src`). Encoding on = H.264 profile encode; encoding off = `-c copy` passthrough.

- `start(stream_name)` — ensure published-path ffmpeg is running (encode or passthrough; non-blocking). Pull start / restore / retry call this; encoding off does **not** start x264.
- `stop(stream_name)` — kill that ffmpeg and drop `{name}__src` path (Dashboard Stop/Delete only — not the Encoding checkbox)
- `status(stream_name)` — `profile`, overlay, `encoding`, `mode`, running, ingest/published ready, lastError
- `update(stream_name, profile=, overlay=, encoding=)` — persist, restart ffmpeg if live mode changes, broadcast `streamux_profile`. Encoding off does not delete pull config, kick MTX readers, or call `_stop_stream_components`.
- `restart(stream_name)` — force encoder restart at current profile; raises `EncodingOff` if encoding is off
- Persistence: `streamux_profiles.json` (`{name: {profile, encoding}}`), `streamux_overlay.json`; one-shot migrate from `overview_rungs.json` / `overview_overlay.json`

### `app.services.hoststats.HostStats`

Reads Linux `/proc` for the StreamUx CM5 card. No `psutil`, no `docker.sock`.

- `snapshot(include_procs=)` — CPU % (delta of `/proc/stat`), memory (`MemTotal`/`MemAvailable`), disk (`shutil.disk_usage` of `DATA_DIR` bind — host filesystem), uptime (`/proc/uptime`), CPU temp (`/sys/class/thermal`, prefers `cpu-thermal`). Process table from `/host/proc` when mounted, else container `/proc`.
- `read_hw()` — Flask wrapper; never raises.

### `app.services.standby.StandbyManager`

Tracks which streams have been seen and marks them standby when publisher disconnects.

- `stream_seen(name, source_info)` — mark active, update `last_seen`
- `stream_gone(name)` — mark standby, record `disconnect_time`
- `remove_stream(name)` — manual removal (user-initiated)
- Background thread: expires streams that exceed `standby_timeout_minutes` (if not 0/infinite)
- Persisted to `data/standby_streams.json`

### `app.services.cleanup` (Auto-Cleanup Service)

Background daemon that manages disk space and recording age. Started on app initialization.

- `start_cleanup_service()` — spawns daemon thread running `cleanup_loop()`
- `cleanup_loop()` — runs every 5 minutes (`CLEANUP_INTERVAL = 300`), reads settings from `server_settings`
- `_cleanup_old_files(max_age_days)` — deletes recording files older than `cleanup_days`
- `_cleanup_for_space(min_free_gb)` — deletes oldest recordings until `min_free_space_gb` is available
- Controlled by: `auto_cleanup_enabled`, `cleanup_days`, `min_free_space_gb` runtime settings

### `app.services.tls.TLSService`

- `get_tls_settings()` / `update_tls_settings(data)` — read/write `data/tls_settings.json`
- `get_cert_status()` — check cert file existence and parse subject/issuer/expiry via openssl
- `generate_self_signed(common_name)` — `openssl req -x509 -newkey rsa:4096`
- `request_letsencrypt(domain, email)` — invoke certbot
- `renew_letsencrypt()` — `certbot renew`

---

## 19. Persistence Files

All in `DATA_DIR` (default `/opt/app/data`, local: `data/`).

| File | Format | Purpose |
|---|---|---|
| `pull_sources.json` | `{stream_name: {source_url, username, password, stopped?}}` | Pull stream configs. Loaded on startup, auto-restored 10s after launch. |
| `streamux_profiles.json` | `{stream_name: {profile: "low"|"medium"|"high", encoding: bool}}` | StreamUx profile + encoding flag per pull. Missing `encoding` = on. Legacy `{name: "medium"}` strings migrate. One-shot migrate from `overview_rungs.json`. |
| `streamux_overlay.json` | `{stream_name: bool}` | Overlay on/off per pull. One-shot migrate from `overview_overlay.json`. |
| `streamux-overlay/` | `{stream}.txt` | Live fps/bitrate text for drawtext. One-shot migrate from `overview-overlay/`. |
| `abr_state.json` | `{streams: ["name1", "name2"]}` | Which streams had ABR enabled. Restored 15s after startup. |
| `abr_settings.json` | `{medium_enabled, high: {...}, medium: {...}, low: {...}}` | ABR rendition settings (resolution, bitrate, audio bitrate per tier). |
| `srt_settings.json` | `{port, latency, maxbw, pbkeylen, passphrase, transtype, ...}` | SRT URL parameter presets. |
| `standby_streams.json` | `{stream_name: {name, first_seen, last_seen, status, disconnect_time, source_info}}` | Standby state. |
| `tls_settings.json` | `{rtsps_enabled, https_enabled, cert_type, ...}` | TLS mode settings. |
| `audit.log` | Text | Timestamped audit log (login, logout, key create/revoke, cert changes, TLS updates). |
| `api_keys.json` | JSON | API keys stored as SHA-256 hashes with name and creation timestamp. |
| `certs/server.crt` | PEM | Active TLS certificate. |
| `certs/server.key` | PEM | Active TLS private key (chmod 600). |
| `logs/app.log` | Text (rotating) | App log. Max 10 MB, 5 backups. |
| `logs/ffmpeg/*.log` | Text | Per-stream FFmpeg stderr logs (`streamux-{name}.log` for StreamUx; ABR logs unchanged). |
| `streams/<name>/recording-*.mov` | Video | Recording outputs. |
| `streams/<name>/*_thumb.jpg` | JPEG | Auto-generated recording thumbnails. |
| `hls/<name>/master.m3u8` | M3U8 | ABR master playlist. |
| `hls/<name>/v<variant>/index.m3u8` | M3U8 | Variant playlist. |
| `hls/<name>/v<variant>/*.ts` | MPEG-TS | HLS segments (auto-deleted by FFmpeg, `hls_list_size=10`). |

---

## 20. Configuration (Environment Variables)

| Variable | Default | Description |
|---|---|---|
| `PORT` | `3000` | Flask HTTP port |
| `MEDIAMTX_API_URL` | `http://127.0.0.1:8889` | MediaMTX management API |
| `MEDIAMTX_RTSP_URL` | `rtsp://127.0.0.1:8554` | RTSP endpoint for FFmpeg ABR input |
| `MEDIAMTX_HLS_URL` | `http://127.0.0.1:8888` | MediaMTX native HLS base URL |
| `SECRET_KEY` | Random (generated) | Flask session secret. Must be set for persistent sessions. |
| `ADMIN_USERNAME` | `admin` | Default admin username |
| `ADMIN_PASSWORD` | `changeme` | Default admin password |
| `STREAMS_DIR` | `/opt/app/streams` | Root directory for recordings |
| `DATA_DIR` | `/opt/app/data` | Persistence files, settings, certs |
| `LOGS_DIR` | `/opt/app/logs` | App and FFmpeg log files |
| `HLS_OUTPUT_DIR` | `/opt/app/hls` | ABR HLS segments output |
| `ACTIVE_CERTS_DIR` | `/opt/app/certs` | Active TLS cert directory |
| `EXTERNAL_CERTS_DIR` | `/opt/app/external-certs` | Mounted external certs |
| `FFMPEG_LOG_DIR` | `$LOGS_DIR/ffmpeg` | Per-stream ABR FFmpeg logs |
| `ENABLE_GPU_ENCODING` | `0` | GPU-accelerated encoding (`1` to enable) |
| `PULL_STREAM_BUFFER_SIZE` | `10485760` (10 MB) | FFmpeg `-buffer_size` for pull streams |
| `PULL_STREAM_MAX_DELAY` | `1000000` (1s µs) | FFmpeg `-max_delay` for pull streams |
| `HLS_SEGMENT_DURATION` | `4` | HLS segment length in seconds |
| `HLS_LIST_SIZE` | `10` | Number of segments in HLS playlist |
| `CORS_ORIGINS` | `*` | Allowed CORS origins |
| `HLS_LOW_LATENCY_MODE` | `false` | Low-latency HLS (shorter segments, faster start) |
| `AUTO_GENERATE_CERTS` | `false` | Auto-generate self-signed TLS certs on startup |
| `LOG_LEVEL` | `INFO` | Python logging level |

---

## 21. Runtime Server Settings

Updated at runtime via `POST /api/settings`. Not persisted across restarts (in-memory only except where noted).

| Key | Type | Range | Description |
|---|---|---|---|
| `segmented_recording` | bool | — | Enable segmented recording via FFmpeg segment muxer. Splits recordings into fixed-duration chunks set by `segment_duration`. |
| `segment_duration` | int | 10–86400 | Segment length if segmented recording is used |
| `max_file_size_gb` | int | 1–1000 | Max recording file size |
| `auto_cleanup_enabled` | bool | — | Auto-delete old recordings |
| `cleanup_days` | int | 1–3650 | Age threshold for cleanup |
| `min_free_space_gb` | int | 1–1000 | Minimum free disk space before cleanup |
| `auto_reconnect` | bool | — | Auto-reconnect on stream loss |
| `reconnect_delay` | int | 1–300 | Seconds between reconnect attempts |
| `max_reconnect_attempts` | int | -1–10000 | -1 = infinite |
| `exponential_backoff` | bool | — | Exponential backoff on reconnect |
| `max_backoff_delay` | int | 1–3600 | Max backoff delay in seconds |
| `health_check_enabled` | bool | — | Periodic health checks |
| `stall_detection_enabled` | bool | — | Detect stalled streams |
| `stall_threshold_seconds` | int | 5–600 | Stall detection threshold |
| `srt_buffer_enabled` | bool | — | Enable SRT buffer module |
| `srt_auto_reconnect` | bool | — | SRT auto-reconnect |
| `srt_reconnect_delay` | int | 1–300 | SRT reconnect delay |
| `srt_max_buffer_seconds` | int | 1–300 | SRT buffer window |
| `rtsp_transport` | str | — | `tcp` or `udp` |
| `connection_timeout` | int | 100000–60000000 | FFmpeg connection timeout (µs) |
| `enable_ffmpeg_reconnect` | bool | — | FFmpeg `-reconnect` flags |
| `standby_enabled` | bool | — | Enable standby tracking |
| `standby_timeout_minutes` | int | 0–14400 | 0 = infinite standby |

---

## 22. Utility Modules

### `app.utils.codec_detection`

- `detect_stream_codec(url, timeout)` — runs `ffprobe -v error -show_streams -of json` on a URL. Returns `{codec, has_data, has_audio, protocol}` or `None`.
- `analyze_recording(file_path)` — full ffprobe analysis of a file: duration, size, video/audio/data streams, all metadata tags.

### `app.utils.thumbnail`

- `generate_thumbnail(video_path, stream_name)` — extracts frame at 10% of duration via `ffmpeg -ss <t> -i <in> -frames:v 1 -q:v 2 <out>`. Returns thumbnail path or `None`.

### `shared.klv` (optional)

- KLV (Key-Length-Value) metadata extraction from MPEG-TS streams.
- Used by Utils page to extract embedded sensor/telemetry data from recordings.
- Falls back gracefully if not installed.

### `shared.srt_buffer` (optional)

- SRT receive buffer with auto-reconnect.
- `get_manager()` returns a manager that tracks buffered SRT streams.
- Falls back gracefully if not installed.

---

## 23. External Process Management (FFmpeg)

Flask spawns FFmpeg child processes for six use cases:

| Use case | Spawner | Lifecycle |
|---|---|---|
| **Recording** | `app.api.recordings` | Started by `/record`, stopped by `/stop-record` (graceful `q` → terminate → kill) or auto-cleaned when FFmpeg exits. Thumbnail generated on clean exit. |
| **ABR transcoding** | `app.services.abr.ABRManager` | Per-stream daemon. Stall-detected and restarted automatically. State persisted. |
| **Pull stream** | `app.api.streams` | Per-stream daemon. Auto-retried on failure (infinite by default). Config persisted. |
| **StreamUx encoder** | `app.services.streamux.StreamuxManager` | One x264 publish per pull onto `{name}` (ingest stays `{name}__src`). Restarts on profile/overlay change. |
| **Test publisher** | `app.api.test` | Temporary (duration-limited or manual stop). Stopped when parent stream is deleted. |
| **Transcode** | `app.api.utils` | Background job via `utils/transcode_video.py`. Progress parsed from stdout. Cancellable via SIGTERM. |

**Graceful stop pattern (recording):**
1. Send `b'q'` to FFmpeg stdin → flushes MOV `moov` atom
2. `process.wait(timeout=10)`
3. `process.terminate()` + `wait(timeout=5)`
4. `process.kill()` + `wait()` as last resort

**Non-blocking stop pattern (ABR):**
- `process.terminate()` with no blocking wait
- Orphan if process is in kernel D-state (uninterruptible sleep)
- Clean up state + HLS dir immediately, start new process

---

## 24. Security Model

| Area | Mechanism |
|---|---|
| Authentication | Flask-Login session cookie; API key via `X-API-Key` header (stored as SHA-256 hash) |
| Default password detection | `_DEFAULT_CREDS` flag; UI displays prominent warning |
| Audit log | Writes timestamped entries for login, logout, key create/revoke, cert changes, TLS updates |
| Input validation | Stream names: `^[a-zA-Z0-9_-]+$` max 64. Filenames: no `..`/`/`/`\`, allowed extensions only. |
| Path traversal prevention | All file operations resolve path and assert it is within `STREAMS_DIR` |
| FFmpeg metadata injection | `sanitize_metadata()` strips `; ' " \ \` $` and truncates to 256 chars |
| CORS | Configurable via `CORS_ORIGINS` env var (default `*`) |
| Rate limiting | Flask-Limiter on login endpoint: 5 requests per minute per IP (optional — degrades gracefully if `flask-limiter` not installed) |
| TLS | RTSPS on port 8555; cert managed in `data/certs/`; key stored chmod 600 |
| Secret key | Must be set via env var for persistent sessions; random fallback with warning |

## License

This material is based upon work supported by the United States Air Force under contract number FA8750-24-S-B079 (Prime Contractor Smart Information Flow Technologies (SIFT)).  Any opinions, findings and conclusions or recommendations expressed in this material are those of the author(s) and do not necessarily reflect the views of the United States Air Force.
 Copyright (c) 2026 RTX BBN Technologies. Licensed to US Government with unlimited rights.
 
This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.
This is distributed in the hope that it will be useful, but without any warranty, without even the implied warranty of merchantability or fitness for a particular purpose.  See the GNU General Public License for more details. https://www.gnu.org/licenses/
