from pydantic import BaseModel

from app.common.schemas.internal import SeriesInfo


class StreamToken(BaseModel):
    indexer_id: str
    torrent_id: str
    file_index: int
    playback_id: str
    imdb_id: str | None = None
    series_info: SeriesInfo | None = None
    transcode_audio: bool = False
    target_codec: str | None = None
    audio_bitrate: str | None = None


class ParsedRangeHeader(BaseModel):
    start_byte: int
    end_byte: int
    content_length: int
