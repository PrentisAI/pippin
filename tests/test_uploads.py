"""Uploading files and folders: a project's files (shared by its runs) and a run's own files."""

import io
import tarfile
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from aurora.api.app import create_app
from aurora.api.routes import campaigns as routes
from aurora.api.routes.files import project_files_dir


@pytest.fixture()
def client():
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture()
def cid(client):
    return client.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]


def put(client, url, path, data, **params):
    return client.put(url, params={"path": path, **params}, content=data)


def test_files_and_folders_upload_list_and_delete(client, cid):
    url = f"/api/campaigns/{cid}/files"
    assert put(client, url, "data/trips (2024).csv", b"day,trips\n1,10\n").status_code == 200
    assert put(client, url, "data/sub/notes.md", b"# notes").status_code == 200
    listing = client.get(url, params={"path": "data"}).json()
    assert [e["name"] for e in listing["entries"]] == ["sub", "trips (2024).csv"]     # folders first
    assert listing["usage_bytes"] > 0 and listing["max_file_bytes"] == 20 * 1024 ** 3
    preview = client.get(f"{url}/preview", params={"path": "data/trips (2024).csv"}).json()
    assert preview["content"].startswith("day,trips")
    for bad in ("../escape.txt", "a/../../b", "x\x01y"):
        assert put(client, url, bad, b"x").status_code == 400
    assert put(client, url, "/etc/passwd", b"x").status_code == 200            # taken as relative:
    assert (project_files_dir(cid) / "etc" / "passwd").read_bytes() == b"x"   # inside the project
    assert client.delete(url, params={"path": "data/sub"}).json()["ok"]
    assert not (project_files_dir(cid) / "data" / "sub").exists()


def test_archives_stay_as_they_are_unless_unpacked(client, cid):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a/b.txt", "hello")
        zf.writestr("../evil.txt", "no")          # must not escape
    url = f"/api/campaigns/{cid}/files"
    assert put(client, url, "raw.zip", buf.getvalue()).status_code == 200
    assert (project_files_dir(cid) / "raw.zip").is_file()                      # kept as is
    r = put(client, url, "raw2.zip", buf.getvalue(), extract="true").json()
    assert r["type"] == "dir" and r["extracted_from"] == "raw2.zip"
    base = project_files_dir(cid)
    assert (base / "raw2" / "a" / "b.txt").read_text() == "hello" and not (base / "raw2.zip").exists()
    assert not (base / "evil.txt").exists() and not (base.parent / "evil.txt").exists()
    tbuf = io.BytesIO()
    with tarfile.open(fileobj=tbuf, mode="w:gz") as tf:
        info = tarfile.TarInfo("x/y.csv")
        info.size = 3
        tf.addfile(info, io.BytesIO(b"1,2"))
    assert put(client, url, "t.tar.gz", tbuf.getvalue(), extract="true").json()["type"] == "dir"
    assert (base / "t" / "x" / "y.csv").read_text() == "1,2"


def test_limits_are_enforced(client, cid):
    assert client.put("/api/settings", json={"changes": {"uploads.max_file_gb": 1e-6}}).status_code == 200
    r = put(client, f"/api/campaigns/{cid}/files", "big.bin", b"x" * 5000)
    assert r.status_code == 413
    assert not any(project_files_dir(cid).glob("*.part")) if project_files_dir(cid).exists() else True


def test_project_files_become_the_inputs_of_a_run(client, cid):
    put(client, f"/api/campaigns/{cid}/files", "data/trips.csv", b"day,trips\n1,10\n")
    eid = client.post(f"/api/campaigns/{cid}/experiments",
                      json={"instruction": "x", "mock_mode": True, "skip_phases": ["ideation", "screening"],
                            "inputs": ["project:data"]}).json()["experiment_id"]
    for _ in range(300):
        if client.get(f"/api/campaigns/{cid}/experiments/{eid}").json()["status"] not in ("starting", "running"):
            break
        time.sleep(0.1)
    run = routes._get_run(cid, eid)
    copied = run.campaign_dir / "inputs" / "data" / "trips.csv"
    assert copied.read_text().startswith("day,trips")
    copied.write_text("changed by an agent")                                  # the original is untouched
    assert (project_files_dir(cid) / "data" / "trips.csv").read_text().startswith("day,trips")
    bad = client.post(f"/api/campaigns/{cid}/experiments",
                      json={"instruction": "x", "mock_mode": True, "inputs": ["project:nope.csv"]})
    assert bad.status_code == 400


def test_upload_into_a_run_and_the_front_desk_sees_project_files(client, cid):
    put(client, f"/api/campaigns/{cid}/files", "data/trips.csv", b"day,trips\n1,10\n")
    camp = routes._ensure_campaign(cid)
    assert "data/trips.csv" in routes._project_context(camp)
    tools = {t.name: t for t in routes._project_file_tools(camp)}
    import asyncio
    assert asyncio.run(tools["read_project_file"].handler({"path": "data/trips.csv"}))["content"].startswith("day")
    eid = client.post(f"/api/campaigns/{cid}/experiments",
                      json={"instruction": "x", "mock_mode": True, "skip_phases": ["ideation", "screening"]}
                      ).json()["experiment_id"]
    r = put(client, f"/api/campaigns/{cid}/experiments/{eid}/workspace/upload", "workspace/inputs/extra.txt", b"more")
    assert r.status_code == 200
    assert (routes._get_run(cid, eid).campaign_dir / "workspace" / "inputs" / "extra.txt").read_text() == "more"


def test_messages_carry_their_attachments(client, cid, monkeypatch):
    from aurora.human.frontdesk import FrontDeskAgent
    seen = []

    async def handle(self, text):
        seen.append(text)
        return {"text": "noted"}

    monkeypatch.setattr(FrontDeskAgent, "handle_user_message", handle)
    put(client, f"/api/campaigns/{cid}/files", "data/trips.csv", b"day,trips\n")
    r = client.post(f"/api/campaigns/{cid}/chat/send", json={"text": "use this", "attachments": ["data/trips.csv"]})
    assert r.status_code == 200, r.text
    assert "data/trips.csv" in seen[-1] and "project's files" in seen[-1]
    history = client.get(f"/api/campaigns/{cid}/chat/history").json()
    assert next(m for m in history if m["role"] == "user")["attachments"] == ["data/trips.csv"]
    assert client.post(f"/api/campaigns/{cid}/chat/send", json={"text": " "}).status_code == 400
    # an attachment alone is a message too
    assert client.post(f"/api/campaigns/{cid}/chat/send", json={"attachments": ["data/trips.csv"]}).status_code == 200
