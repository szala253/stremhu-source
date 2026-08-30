from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import anyio
from fastapi import Request

from app.common.constants import CHUNK_SIZE
from app.common.logger import logger

if TYPE_CHECKING:
    pass


class AudioTranscoder:
    """
    On-the-fly streaming audio transcoder using FFmpeg.
    - Copies video streams untouched (-c:v copy) to preserve original video quality and minimize CPU usage.
    - Copies subtitle streams untouched (-c:s copy).
    - Transcodes audio streams to AAC or AC3 to provide universal smart TV and device compatibility.
    - Streams output via Matroska container (video/x-matroska).
    """

    def __init__(
        self,
        target_codec: str = "ac3",
        bitrate: str = "640k",
        ffmpeg_bin: str = "ffmpeg",
    ):
        self.target_codec = target_codec
        self.bitrate = bitrate
        self.ffmpeg_bin = ffmpeg_bin

    @staticmethod
    def is_available() -> bool:
        """Checks if the ffmpeg binary is available in the system PATH."""
        return shutil.which("ffmpeg") is not None

    async def transcode_stream(
        self,
        input_stream: AsyncIterator[bytes],
        request: Request,
    ) -> AsyncIterator[bytes]:
        """
        Pipes the input video chunk stream into FFmpeg, transcodes audio on the fly,
        and yields output chunks for StreamingResponse.
        """
        if not self.is_available():
            logger.error("FFmpeg not found in system PATH. Audio transcoding cannot proceed.")
            async for chunk in input_stream:
                if await request.is_disconnected():
                    break
                yield chunk
            return

        cmd = [
            self.ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "copy",
            "-c:a",
            self.target_codec,
            "-b:a",
            self.bitrate,
            "-c:s",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            "-f",
            "matroska",
            "pipe:1",
        ]

        logger.info(
            f"Starting FFmpeg audio transcoding process (codec: {self.target_codec}, bitrate: {self.bitrate})"
        )

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        assert proc.stdin is not None
        assert proc.stdout is not None

        async def feed_stdin():
            try:
                async for chunk in input_stream:
                    if proc.returncode is not None or await request.is_disconnected():
                        break
                    proc.stdin.write(chunk)
                    await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, anyio.get_cancelled_exc_class(), asyncio.CancelledError):
                pass
            except Exception as e:
                logger.debug(f"AudioTranscoder stdin feed notice: {e}")
            finally:
                if proc.stdin and not proc.stdin.is_closing():
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass

        feed_task = asyncio.create_task(feed_stdin())

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
            logger.error(f"Error during audio transcoding stream: {e}")
        finally:
            if not feed_task.done():
                feed_task.cancel()
                try:
                    await feed_task
                except (asyncio.CancelledError, Exception):
                    pass

            if proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=1.0)
                except Exception:
                    try:
                        proc.kill()
                        await proc.wait()
                    except Exception:
                        pass

            if proc.stderr:
                try:
                    stderr_output = await proc.stderr.read()
                    if stderr_output and proc.returncode not in (None, 0, -15, -9):
                        logger.warning(
                            f"FFmpeg process exited with code {proc.returncode}: {stderr_output.decode('utf-8', errors='ignore')}"
                        )
                except Exception:
                    pass

            logger.info("FFmpeg audio transcoding process cleaned up.")
