from __future__ import annotations

import asyncio
import json
import math
import shutil
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import anyio
from fastapi import Request

from app.common.logger import logger

if TYPE_CHECKING:
    pass

_duration_cache: dict[str, float] = {}


class HLSManager:
    """
    Manages HLS VOD playlists and on-demand segment generation for Stremio.
    Provides exact full video duration on the player timeline and instant seeking.
    """

    SEGMENT_DURATION = 6.0

    @staticmethod
    def is_ffprobe_available() -> bool:
        return shutil.which("ffprobe") is not None

    @classmethod
    async def get_stream_duration(
        cls,
        raw_stream_url: str,
        cache_key: str,
        estimated_duration: float = 7200.0,
    ) -> float:
        """
        Extracts the exact container duration using ffprobe with in-memory caching.
        """
        if cache_key in _duration_cache:
            return _duration_cache[cache_key]

        if not cls.is_ffprobe_available():
            _duration_cache[cache_key] = estimated_duration
            return estimated_duration

        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-tls_verify",
            "0",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            raw_stream_url,
        ]

        try:
            logger.info(f"Probing stream duration for {cache_key} via ffprobe...")
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode == 0 and stdout:
                data = json.loads(stdout.decode("utf-8", errors="ignore"))
                dur_str = data.get("format", {}).get("duration")
                if dur_str:
                    duration = float(dur_str)
                    if duration > 0:
                        _duration_cache[cache_key] = duration
                        logger.info(f"Stream {cache_key} duration probed: {duration:.2f} seconds")
                        return duration
        except Exception as e:
            logger.warning(f"Failed to probe duration for {cache_key}: {e}")

        _duration_cache[cache_key] = estimated_duration
        return estimated_duration

    @classmethod
    def generate_master_playlist(cls) -> str:
        """
        Generates the HLS master playlist for Stremio.
        """
        return (
            "#EXTM3U\n"
            "#EXT-X-VERSION:3\n"
            '#EXT-X-STREAM-INF:BANDWIDTH=15000000,CODECS="avc1.640028,ac-3"\n'
            "index.m3u8\n"
        )

    @classmethod
    def generate_media_playlist(cls, total_duration: float) -> str:
        """
        Generates a VOD HLS media playlist with exact full length.
        """
        segment_count = max(1, math.ceil(total_duration / cls.SEGMENT_DURATION))
        target_dur = math.ceil(cls.SEGMENT_DURATION)

        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            f"#EXT-X-TARGETDURATION:{target_dur}",
            "#EXT-X-MEDIA-SEQUENCE:0",
            "#EXT-X-PLAYLIST-TYPE:VOD",
        ]

        for i in range(segment_count):
            start_time = i * cls.SEGMENT_DURATION
            if i == segment_count - 1:
                seg_dur = total_duration - start_time
                if seg_dur <= 0:
                    seg_dur = cls.SEGMENT_DURATION
            else:
                seg_dur = cls.SEGMENT_DURATION

            lines.append(f"#EXTINF:{seg_dur:.3f},")
            lines.append(f"segment_{i}.ts")

        lines.append("#EXT-X-ENDLIST\n")
        return "\n".join(lines)

    @classmethod
    async def generate_segment_stream(
        cls,
        raw_stream_url: str,
        segment_index: int,
        total_duration: float,
        request: Request,
        target_codec: str = "ac3",
        bitrate: str = "640k",
    ) -> AsyncIterator[bytes]:
        """
        Generates an individual 6-second MPEG-TS segment on the fly via FFmpeg.
        Copies video (-c:v copy) and transcodes audio to Dolby Digital (AC3 640k).
        """
        start_time = segment_index * cls.SEGMENT_DURATION
        duration = min(cls.SEGMENT_DURATION, max(0.1, total_duration - start_time))

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-tls_verify",
            "0",
            "-ss",
            f"{start_time:.3f}",
            "-i",
            raw_stream_url,
            "-t",
            f"{duration:.3f}",
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "copy",
            "-c:a",
            target_codec,
            "-b:a",
            bitrate,
            "-c:s",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            "-f",
            "mpegts",
            "pipe:1",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        assert proc.stdout is not None

        CHUNK_SIZE = 64 * 1024
        try:
            while True:
                if await request.is_disconnected():
                    break
                chunk = await proc.stdout.read(CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk
        except anyio.get_cancelled_exc_class():
            pass
        except Exception as e:
            logger.debug(f"Error streaming segment {segment_index}: {e}")
        finally:
            if proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=0.5)
                except Exception:
                    try:
                        proc.kill()
                        await proc.wait()
                    except Exception:
                        pass
