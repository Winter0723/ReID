"""Catalog, protected preview and frozen licensing metadata for bundled music."""

import json

import pytest

from test_scenic_editing_api import seed


def test_music_catalog_previews_require_auth_or_path_bound_signature(client, register_scenic_user):
    user = register_scenic_user()
    endpoint = "/api/v1/scenic/editing/catalog"
    assert client.get(endpoint).status_code == 401
    response = client.get(endpoint, headers=user["headers"])
    assert response.status_code == 200, response.text
    tracks = response.json()["music"]
    assert len(tracks) == 6
    downloaded = [track for track in tracks if track["origin"] == "download"]
    assert len(downloaded) == 3
    for track in tracks:
        preview = track["preview_url"]
        bare_url = preview.split("?")[0]
        assert client.get(bare_url).status_code == 401
        played = client.get(preview, headers={"Range": "bytes=0-1023"})
        assert played.status_code == 206, played.text
        assert len(played.content) == 1024
        assert played.headers["content-type"] == ("audio/mpeg" if track["origin"] == "download" else "audio/wav")
        assert played.headers["cache-control"] == "private, no-store"
    signed = downloaded[0]["preview_url"]
    tampered = signed.replace(downloaded[0]["id"], downloaded[1]["id"])
    assert client.get(tampered).status_code == 401
    assert client.get("/api/v1/scenic/editing/music/missing/preview", headers=user["headers"]).status_code == 404


def test_downloaded_music_is_saved_and_frozen_with_provenance(client, register_scenic_user, monkeypatch):
    import scenic_database as database
    from app.services.job_manager import job_manager
    from app.services.scenic_templates import DOWNLOADED_MUSIC

    monkeypatch.setattr(job_manager, "enqueue", lambda *args: None)
    user, _, tourist = seed(client, register_scenic_user)
    base = f"/api/v1/scenic/tourists/{tourist}"
    created = client.post(base + "/drafts/auto", headers=user["headers"], json={"music_id": "oga_revelation"})
    assert created.status_code == 200, created.text
    draft = created.json()
    assert draft["data"]["music_id"] == "oga_revelation"
    draft["data"]["music_id"] = "oga_happy_moments"
    draft["data"]["music_volume"] = .4
    saved = client.put(base + f"/drafts/{draft['id']}", headers=user["headers"], json={"revision": 1, "data": draft["data"]})
    assert saved.status_code == 200, saved.text
    submitted = client.post(base + "/vlog", headers=user["headers"], json={"draft_id": draft["id"], "revision": 2})
    assert submitted.status_code == 200, submitted.text
    assert len(submitted.json()["vlog"]["variants"]) == 2
    with database.SessionLocal() as db:
        snapshot = json.loads(db.get(database.ScenicVlogJob, submitted.json()["vlog"]["id"]).snapshot_json)
        assert snapshot["music_id"] == "oga_happy_moments"
        assert snapshot["music_volume"] == .4
        assert snapshot["music_sha256"] == DOWNLOADED_MUSIC["oga_happy_moments"]["sha256"]
        assert snapshot["music_provenance"]["author"] == "Centurion_of_war"
        assert snapshot["music_provenance"]["license"] == "CC0-1.0"
        assert snapshot["music_provenance"]["source_url"] == "https://opengameart.org/content/happy-moments"
        assert "preview_url" not in snapshot["music_provenance"]


@pytest.mark.parametrize("music_id", ["none", "scenic_morning", "oga_revelation"])
def test_submitting_only_requires_the_selected_music(client, register_scenic_user, monkeypatch, tmp_path, music_id):
    import scenic_database as database
    from app.services.job_manager import job_manager
    from app.services import scenic_templates

    monkeypatch.setattr(job_manager, "enqueue", lambda *args: None)
    user, _, tourist = seed(client, register_scenic_user)
    base = f"/api/v1/scenic/tourists/{tourist}"
    created = client.post(base + "/drafts/auto", headers=user["headers"], json={"music_id": music_id})
    assert created.status_code == 200, created.text
    draft = created.json()
    # All downloaded files are now missing, while legacy synths remain usable.
    monkeypatch.setattr(scenic_templates, "MUSIC_ASSET_DIR", tmp_path / "missing-music")
    saved = client.put(base + f"/drafts/{draft['id']}", headers=user["headers"], json={"revision": 1, "data": draft["data"]})
    assert saved.status_code == 200, saved.text
    submitted = client.post(base + "/vlog", headers=user["headers"], json={"draft_id": draft["id"], "revision": 2})
    if music_id == "oga_revelation":
        assert submitted.status_code == 503, submitted.text
        assert "配乐资源缺失" in submitted.json()["detail"]
        with database.SessionLocal() as db:
            assert db.query(database.ScenicVlogJob).count() == 0
    else:
        assert submitted.status_code == 200, submitted.text
        if music_id == "none":
            with database.SessionLocal() as db:
                snapshot = json.loads(db.get(database.ScenicVlogJob, submitted.json()["vlog"]["id"]).snapshot_json)
                assert "music_sha256" not in snapshot and "music_provenance" not in snapshot
