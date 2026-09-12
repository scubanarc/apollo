from unittest.mock import MagicMock

import pytest

from apollo import estools


@pytest.fixture
def audio_file(config, monkeypatch):
    path = config.directory('MUSIC_FOLDER') / 'song.mp3'
    path.write_bytes(b'0123456789abcdef')
    es = MagicMock()
    es.get.return_value = {'_source': {'url': str(path)}}
    monkeypatch.setattr(estools, 'get_es', lambda: (es, 'music'))
    return path, es


def test_stream_ranges_and_head(client, audio_file):
    response = client.get('/stream?entry_id=exact', headers={'Range': 'bytes=4-7'})
    assert response.status_code == 206
    assert response.data == b'4567'
    assert response.headers['Content-Range'] == 'bytes 4-7/16'
    assert response.mimetype == 'audio/mpeg'
    assert client.head('/stream?entry_id=exact').headers['Content-Length'] == '16'
    assert client.get('/stream?entry_id=exact', headers={'Range': 'bytes=99-100'}).status_code == 416
    audio_file[1].get.assert_called_with(index='music', id='exact')


def test_stream_song_uses_best_match(client, audio_file, monkeypatch):
    result = {'hits': {'hits': []}}
    monkeypatch.setattr(estools, 'search_es', lambda *args: result)
    monkeypatch.setattr(estools, 'pick_best_hit', lambda hits: (
        {'hit': {'_source': {'url': str(audio_file[0])}}}, None, None))
    assert client.get('/stream?song=Artist+-+Song').data == audio_file[0].read_bytes()
    monkeypatch.setattr(estools, 'pick_best_hit', lambda hits: (None, None, None))
    assert client.get('/stream?song=Artist+-+Missing').status_code == 404


def test_stream_boundary_missing_and_type(client, audio_file, tmp_path):
    path, es = audio_file
    outside = tmp_path / 'private.mp3'
    outside.write_bytes(b'private')
    path.unlink()
    path.symlink_to(outside)
    assert client.get('/stream?entry_id=exact').status_code == 400
    es.get.return_value['_source']['url'] = str(path.parent / 'missing.mp3')
    assert client.get('/stream?entry_id=exact').status_code == 404
    private = path.parent / 'settings.yml'
    private.write_text('private')
    es.get.return_value['_source']['url'] = str(private)
    assert client.get('/stream?entry_id=exact').status_code == 400


@pytest.mark.parametrize('query', ['', '?song=invalid', '?song=%23comment', '?song=A+-+B&entry_id=x'])
def test_stream_validates_selection(client, query):
    assert client.get('/stream' + query).status_code == 400


def test_stream_requires_login(client, app, audio_file):
    app.config['API_TOKEN'] = 'test-token'
    assert client.get('/stream?entry_id=exact').status_code == 302
    with client.session_transaction() as session:
        session['authenticated'] = True
    assert client.get('/stream?entry_id=exact').status_code == 200
