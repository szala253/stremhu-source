from __future__ import annotations

import pytest

from app.modules.media_attributes.constants import DTS_ATTRIBUTE_KEYS, MediaAttributeKey
from app.modules.media_attributes.models import MediaAttributeModel
from app.modules.media_attributes.parser import parse_torrent_name
from app.modules.stream.schemas import StreamToken
from app.modules.stream.transcoder import AudioTranscoder
from app.modules.stream.utils.stream_token import generate_stream_token, parse_stream_token
from app.modules.stremio.schemas import StremioStream
from app.modules.torrent_streams.schemas import TorrentStream


def test_dts_attribute_detection():
    # DTS-HD MA 7.1
    attrs1 = parse_torrent_name("Movie.2024.1080p.BluRay.x264.DTS-HD.MA.7.1.HUN")
    attr_ids1 = {a.id for a in attrs1}
    assert MediaAttributeKey.DTS_HD_MA in attr_ids1
    assert MediaAttributeKey.CH_7_1 in attr_ids1
    assert any(a.id in DTS_ATTRIBUTE_KEYS for a in attrs1)

    # DTS-HD HRA 5.1
    attrs2 = parse_torrent_name("Movie.2024.1080p.BluRay.DTS-HD.HRA.5.1.Eng")
    attr_ids2 = {a.id for a in attrs2}
    assert MediaAttributeKey.DTS_HD_MA in attr_ids2
    assert MediaAttributeKey.CH_5_1 in attr_ids2
    assert any(a.id in DTS_ATTRIBUTE_KEYS for a in attrs2)

    # Standard DTS 5.1
    attrs3 = parse_torrent_name("Movie.2023.1080p.BDRip.x264.DTS.5.1-GROUP")
    attr_ids3 = {a.id for a in attrs3}
    assert MediaAttributeKey.DTS in attr_ids3
    assert MediaAttributeKey.CH_5_1 in attr_ids3
    assert any(a.id in DTS_ATTRIBUTE_KEYS for a in attrs3)

    # DTS:X
    attrs4 = parse_torrent_name("Movie.2024.2160p.UHD.Remux.DV.DTS-X.7.1")
    attr_ids4 = {a.id for a in attrs4}
    assert MediaAttributeKey.DTS_X in attr_ids4
    assert any(a.id in DTS_ATTRIBUTE_KEYS for a in attrs4)

    # DTS-ES
    attrs5 = parse_torrent_name("Movie.2005.1080p.BluRay.DTS-ES.6.1")
    attr_ids5 = {a.id for a in attrs5}
    assert MediaAttributeKey.DTS in attr_ids5
    assert any(a.id in DTS_ATTRIBUTE_KEYS for a in attrs5)

    # Non-DTS (e.g. Dolby Digital Plus / EAC3)
    attrs_non_dts = parse_torrent_name("Movie.2024.1080p.WEB-DL.DDP5.1.Atmos.H.264")
    attr_ids_non_dts = {a.id for a in attrs_non_dts}
    assert MediaAttributeKey.DD_PLUS in attr_ids_non_dts
    assert not any(a.id in DTS_ATTRIBUTE_KEYS for a in attrs_non_dts)


def test_stream_token_transcoding_roundtrip():
    token_payload = StreamToken(
        indexer_id="bithumen",
        torrent_id="123456",
        file_index=0,
        playback_id="test-playback-id",
        transcode_audio=True,
        target_codec="aac",
        audio_bitrate="384k",
    )

    encoded = generate_stream_token(token_payload)
    decoded = parse_stream_token(encoded)

    assert decoded.indexer_id == "bithumen"
    assert decoded.torrent_id == "123456"
    assert decoded.file_index == 0
    assert decoded.transcode_audio is True
    assert decoded.target_codec == "aac"
    assert decoded.audio_bitrate == "384k"


def test_audio_transcoder_initialization():
    transcoder = AudioTranscoder(target_codec="aac", bitrate="384k")
    assert transcoder.target_codec == "aac"
    assert transcoder.bitrate == "384k"
    assert transcoder.ffmpeg_bin == "ffmpeg"


def test_stremio_stream_transcoded_view():
    dts_attr = MediaAttributeModel(
        id=MediaAttributeKey.DTS_HD_MA,
        name="DTS-HD Master Audio",
        short_name="DTS-HD MA",
        preference_id="audio_quality",
        pattern=None,
    )
    res_attr = MediaAttributeModel(
        id=MediaAttributeKey.R1080P,
        name="Full HD (1080p)",
        short_name="1080p",
        preference_id="resolution",
        pattern=None,
    )

    from app.modules.indexer_accounts.models import IndexerAccountModel
    from app.modules.indexer_definitions.models import IndexerDefinitionModel

    indexer_def = IndexerDefinitionModel(
        id="ncore",
        name="nCore",
        url="https://ncore.pro",
        details_path="/torrents.php?action=details&id={id}",
    )
    indexer_acc = IndexerAccountModel(
        indexer_id="ncore",
        username="user",
        password="pwd",
    )
    indexer_acc.indexer_definition = indexer_def

    dummy_stream = TorrentStream(
        indexer_account=indexer_acc,
        torrent_id="999",
        info_hash="abcdef1234567890",
        torrent_name="Movie.2024.1080p.BluRay.DTS-HD.MA.7.1",
        file_name="Movie.2024.1080p.BluRay.DTS-HD.MA.7.1.mkv",
        file_size=1024 * 1024 * 1024 * 4,
        file_index=0,
        play_url="http://localhost:7070/api/key/stream/raw-token",
        transcoded_play_url="http://localhost:7070/api/key/stream/transcoded-token",
        attributes=[dts_attr, res_attr],
        is_persisted_torrent=False,
    )

    assert dummy_stream.has_dts is True

    # Direct stream
    direct_stremio = StremioStream.from_imdb_torrent_stream(dummy_stream, transcoded=False)
    assert direct_stremio.url == "http://localhost:7070/api/key/stream/raw-token"
    assert "🔄 AAC" not in direct_stremio.name

    # Transcoded stream
    transcoded_stremio = StremioStream.from_imdb_torrent_stream(dummy_stream, transcoded=True)
    assert transcoded_stremio.url == "http://localhost:7070/api/key/stream/transcoded-token"
    assert "🔄 AAC" in transcoded_stremio.name
    assert "Transcoded from DTS" in transcoded_stremio.description
