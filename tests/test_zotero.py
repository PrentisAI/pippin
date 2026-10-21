"""Zotero: collections come into projects (checked), papers go back (tagged), nothing is deleted."""

import json
import re

import httpx
import pytest
from fastapi.testclient import TestClient

from aurora.literature import indexes as indexes_mod
from aurora.literature import zotero as z
from aurora.literature.library import library
from aurora.literature.records import Paper
from aurora.literature.service import ProjectLiterature, paper_card
from aurora.literature.zotero import item_reference, paper_item, zotero_store
from tests.test_literature import BEHRENS, KULD, FakeIndexes

PDF = b"%PDF-1.4 a highlighted paper"


class FakeZotero:
    """A Zotero web API with one user library and one group, in memory."""

    def __init__(self, write=True):
        self.version = 10
        self.write = write
        self.items = {
            "I1": {"key": "I1", "version": 9, "data": {
                "key": "I1", "itemType": "journalArticle", "title": BEHRENS.title, "date": "2012-05",
                "creators": [{"creatorType": "author", "firstName": "Malte", "lastName": "Behrens"}],
                "DOI": BEHRENS.doi, "extra": "Citation Key: behrens2012active", "collections": ["COLA"]}},
            "I2": {"key": "I2", "version": 9, "data": {
                "key": "I2", "itemType": "thesis", "title": "Zinc on copper: my doctoral thesis", "date": "2019",
                "creators": [{"creatorType": "author", "firstName": "Linda", "lastName": "Fei"}], "collections": ["COLA"]}},
            # a PDF dragged in on its own, named as ZotFile names files (a cut-off title)
            "P1": {"key": "P1", "version": 9, "data": {
                "key": "P1", "itemType": "attachment", "linkMode": "imported_file", "contentType": "application/pdf",
                "title": "Kuld et al_2016_Quantifying the promotion of Cu catalysts by Zn.pdf", "collections": ["COLA"]}},
            "OLD": {"key": "OLD", "version": 3, "data": {
                "key": "OLD", "itemType": "journalArticle", "title": KULD.title, "date": "2016", "DOI": KULD.doi,
                "creators": [{"creatorType": "author", "lastName": "Kuld", "firstName": "Stine"}], "collections": ["COLB"]}},
        }
        self.children = {
            "I1": [{"key": "A1", "version": 9, "data": {"key": "A1", "itemType": "attachment", "linkMode": "imported_file",
                                                        "contentType": "application/pdf"}},
                   {"key": "N1", "version": 9, "data": {"key": "N1", "itemType": "note",
                                                        "note": "<p>Zn sits at the <b>steps</b>.</p>"}},
                   {"key": "N2", "version": 9, "data": {"key": "N2", "itemType": "note",
                                                        "note": "<p>Comment: 23 pages, 9 figures</p>"}},
                   {"key": "L1", "version": 9, "data": {"key": "L1", "itemType": "attachment", "linkMode": "linked_file",
                                                        "contentType": "application/pdf"}}],
            "P1": [{"key": "AN2", "version": 9, "data": {"key": "AN2", "itemType": "annotation",
                                                         "annotationText": "ZnO promotes Cu by 15-fold", "annotationPageLabel": "2"}}],
            "A1": [{"key": "AN1", "version": 9, "data": {"key": "AN1", "itemType": "annotation", "annotationType": "highlight",
                                                         "annotationText": "Zn decorates the Cu steps",
                                                         "annotationComment": "the key result", "annotationPageLabel": "3"}}],
        }
        self.created, self.patched, self.deleted, self.requests = [], [], [], []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, q = request.url.path, dict(request.url.params)
        self.requests.append((request.method, path, q))
        if request.headers.get("Zotero-API-Key") != "secret-zotero-key-123":
            return httpx.Response(403)
        if request.method == "DELETE":
            self.deleted.append(path)
            return httpx.Response(204)
        hdr = {"Last-Modified-Version": str(self.version)}
        if path == "/keys/current":
            return httpx.Response(200, json={"userID": 42, "username": "linda", "access": {
                "user": {"library": True, "files": True, "notes": True, "write": self.write},
                "groups": {"all": {"library": True, "write": False}}}})
        if path == "/users/42/groups":
            return httpx.Response(200, json=[{"id": 7, "data": {"name": "Catalysis lab"}}], headers={**hdr, "Total-Results": "1"})
        if path == "/users/42/collections":
            cols = [{"key": "COLA", "data": {"name": "Methanol on Cu", "parentCollection": False}, "meta": {"numItems": 2}},
                    {"key": "COLB", "data": {"name": "Reading list", "parentCollection": False}, "meta": {"numItems": 1}},
                    {"key": "COLC", "data": {"name": "Old", "parentCollection": "COLB"}, "meta": {"numItems": 0}}]
            return httpx.Response(200, json=cols, headers={**hdr, "Total-Results": "3"})
        if path == "/groups/7/collections":
            return httpx.Response(200, json=[{"key": "G1", "data": {"name": "Shared"}, "meta": {"numItems": 5}}],
                                  headers={**hdr, "Total-Results": "1"})
        m = re.fullmatch(r"/users/42/collections/(\w+)/items/top", path)
        if m:
            since = int(q.get("since", 0))
            rows = [i for i in self.items.values() if m.group(1) in i["data"]["collections"] and i["version"] > since]
            return httpx.Response(200, json=rows, headers={**hdr, "Total-Results": str(len(rows))})
        m = re.fullmatch(r"/users/42/items/(\w+)/children", path)
        if m:
            rows = self.children.get(m.group(1), [])
            return httpx.Response(200, json=rows, headers={**hdr, "Total-Results": str(len(rows))})
        if path in ("/users/42/items/A1/file", "/users/42/items/P1/file"):
            return httpx.Response(200, content=PDF)
        if path == "/users/42/items" and request.method == "GET":
            text = q.get("q", "").lower()
            rows = [i for i in self.items.values() if text and text in json.dumps(i["data"]).lower()]
            return httpx.Response(200, json=rows, headers={**hdr, "Total-Results": str(len(rows))})
        if path == "/users/42/items" and request.method == "POST":
            if not self.write:
                return httpx.Response(403)
            items = json.loads(request.content)
            ok = {}
            for k, it in enumerate(items):
                key = f"NEW{len(self.created) + 1}"
                self.created.append(it)
                self.version += 1
                self.items[key] = {"key": key, "version": self.version, "data": {**it, "key": key}}
                ok[str(k)] = {"key": key}
            return httpx.Response(200, json={"successful": ok, "failed": {}})
        m = re.fullmatch(r"/users/42/items/(\w+)", path)
        if m and request.method == "PATCH":
            if not self.write:
                return httpx.Response(403)
            body = json.loads(request.content)
            self.patched.append((m.group(1), body, request.headers.get("If-Unmodified-Since-Version")))
            self.version += 1
            it = self.items[m.group(1)]
            it["data"].update(body)
            it["version"] = self.version
            return httpx.Response(204)
        return httpx.Response(404)


@pytest.fixture()
def zot(monkeypatch):
    fake = FakeZotero()
    monkeypatch.setattr(indexes_mod, "_shared", FakeIndexes())
    monkeypatch.setattr(z, "http_client", httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    return fake


@pytest.fixture()
def client(zot):
    from aurora.api.app import create_app
    return TestClient(create_app())


def test_items_and_papers_translate_both_ways():
    ref = item_reference({"title": "Attention", "creators": [{"creatorType": "author", "lastName": "Vaswani", "firstName": "A."}],
                          "date": "June 2017", "url": "https://arxiv.org/abs/1706.03762", "extra": "Citation Key: vaswani2017"})
    assert (ref.first_author, ref.year, ref.arxiv_id) == ("Vaswani", 2017, "1706.03762")
    item = paper_item(Paper(title="T", authors=["Malte Behrens", "Studt, Felix"], year=2012, doi="10.1/x", venue="Science"), "COL")
    assert item["itemType"] == "journalArticle" and item["collections"] == ["COL"] and item["tags"] == [{"tag": "aurora"}]
    assert item["creators"][1] == {"creatorType": "author", "firstName": "Felix", "lastName": "Studt"}
    pre = paper_item(Paper(title="T", authors=["A B"], year=2017, arxiv_id="1706.03762"), "COL")
    assert pre["itemType"] == "preprint" and pre["archiveID"] == "arXiv:1706.03762"


def test_connect_choose_collections_and_disconnect(client, zot):
    cid = client.post("/api/campaigns/quick", json={"prompt": "Methanol on Cu"}).json()["campaign_id"]
    assert client.get("/api/zotero").json() == {"connected": False}
    assert client.post("/api/zotero/connect", json={"method": "web", "api_key": "wrong-key-0000"}).status_code == 400
    st = client.post("/api/zotero/connect", json={"method": "web", "api_key": "secret-zotero-key-123"}).json()
    assert st["connected"] and st["username"] == "linda" and st["can_write"] and "secret-zotero-key-123" not in json.dumps(st)
    cols = client.get("/api/zotero/collections").json()
    names = {c["name"]: c for c in cols["collections"]}
    assert set(names) == {"My Library / Methanol on Cu", "My Library / Reading list", "My Library / Reading list / Old",
                          "Catalysis lab / Shared"}
    assert names["My Library / Methanol on Cu"]["campaign_id"] == cid and cols["suggested"]       # matched by name
    st = client.put("/api/zotero", json={"mappings": [
        {"library": "user", "collection_key": "COLA", "name": "My Library / Methanol on Cu", "campaign_id": cid},
        {"library": "user", "collection_key": "COLB", "name": "My Library / Reading list", "campaign_id": ""}],
        "options": {"notes": False}}).json()
    assert [(m["collection_key"], m["project"]) for m in st["mappings"]] == [("COLA", "Methanol on Cu"), ("COLB", "Your Library")]
    assert st["options"]["notes"] is False and st["options"]["pdfs"] is True
    assert client.put("/api/zotero", json={"mappings": [
        {"library": "user", "collection_key": "COLA", "campaign_id": "p_not_yours"}]}).status_code == 403
    assert client.delete("/api/zotero").json() == {"connected": False}
    assert zotero_store.maps("local") == [] and zot.deleted == []


async def test_a_collection_comes_into_its_project(client, zot):
    cid = client.post("/api/campaigns/quick", json={"prompt": "Methanol"}).json()["campaign_id"]
    client.post("/api/zotero/connect", json={"method": "web", "api_key": "secret-zotero-key-123"})
    zotero_store.set_maps("local", [{"library": "user", "collection_key": "COLA", "name": "Methanol", "campaign_id": cid}])
    report = await z.sync("local", "You")
    assert (report.seen, report.added, report.vouched, report.pdfs, report.linked_pdfs, report.notes) == (3, 2, 1, 2, 1, 3)
    assert "linked files on your computer" in report.to_dict()["problems"][0]
    lit = client.get(f"/api/campaigns/{cid}/literature").json()
    # the standalone PDF was recognised from its file name, its file copied and its highlight kept
    (kuld,) = [x for x in lit["papers"] if x["key"] == "Kuld16"]
    assert kuld["pdf_file"] == "papers/Kuld16.pdf" and [n["text"] for n in kuld["notes"]] == ["ZnO promotes Cu by 15-fold"]
    (p,) = [x for x in lit["papers"] if x["key"] == "Behrens12"]
    assert p["key"] == "Behrens12" and p["source"] == "zotero" and p["in_zotero"] and p["added_by"] == "Your Zotero"
    assert p["pdf_file"] == "papers/behrens2012active.pdf"
    assert {(n["kind"], n["text"], n["page"]) for n in p["notes"]} == {
        ("highlight", "Zn decorates the Cu steps", "3"), ("note", "Zn sits at the steps.", "")}
    # the thesis no index has is kept as vouched for, not as a checked paper
    (thesis,) = lit["refs"]
    assert thesis["resolution"] == "have" and thesis["reference"]["title"].startswith("Zinc on copper")
    assert lit["zotero"]["collections"] == ["Methanol"] and lit["zotero"]["can_send"]
    # Better BibTeX keys in the export
    assert "@article{behrens2012active," in client.get(f"/api/campaigns/{cid}/literature/export.bib").text
    # the agents see the researcher's highlight with the paper
    (entry,) = ProjectLiterature(cid).bibliography() or [{**library.paper("Behrens12"), "notes": library.notes(cid)[p["paper_id"]]}]
    assert "Researcher's highlight (p. 3): “Zn decorates the Cu steps” — the key result" in paper_card(entry)
    card = paper_card({**entry, "notes": [{"kind": "highlight", "text": "force", "page": "1", "comment": ""},
                                          {"kind": "highlight", "text": "a molecu- lar simulation of the cell", "page": "2"}]})
    assert "“force”" not in card and "a molecular simulation of the cell" in card
    # the next sync asks only for what changed
    zot.requests.clear()
    again = await z.sync("local", "You")
    assert again.seen == 0
    assert any(q.get("since") == "10" for _, path, q in zot.requests if path.endswith("/items/top"))


async def test_project_papers_go_to_the_collection_and_nothing_is_deleted(client, zot):
    del zot.items["P1"]          # Kuld16 is only in another collection here
    cid = client.post("/api/campaigns/quick", json={"prompt": "Methanol"}).json()["campaign_id"]
    client.post("/api/zotero/connect", json={"method": "web", "api_key": "secret-zotero-key-123"})
    zotero_store.set_maps("local", [{"library": "user", "collection_key": "COLA", "name": "Methanol", "campaign_id": cid}])
    await z.sync("local", "You")
    # an agent cites Kuld16 (already in the person's library, in another collection) and Vaswani17 (new);
    # a made-up reference is flagged and never sent
    lit = ProjectLiterature(cid, run_id="r1")
    await lit.check_text("[ref: Quantifying the promotion of Cu catalysts by ZnO for methanol synthesis; Kuld; 2016] "
                         "[ref: Attention Is All You Need; Vaswani; 2017] [ref: A zinc law that does not exist; Smith; 2024]",
                         by="p01", place="Ideation")
    report = await z.sync("local", "You")
    assert report.pushed == 2
    (created,) = zot.created
    assert created["title"] == "Attention Is All You Need" and created["itemType"] == "preprint"
    assert created["collections"] == ["COLA"] and created["tags"] == [{"tag": "aurora"}]
    (patched,) = zot.patched
    assert patched[0] == "OLD" and patched[1]["collections"] == ["COLB", "COLA"] and patched[2] == "3"
    assert zot.deleted == [] and all("does not exist" not in json.dumps(c) for c in zot.created)
    # sent once: the next sync sends nothing again
    assert (await z.sync("local", "You")).pushed == 0 and len(zot.created) == 1
    lit_view = client.get(f"/api/campaigns/{cid}/literature").json()
    assert all(p["in_zotero"] for p in lit_view["papers"])


def test_sending_needs_write_access_and_a_collection(client, zot):
    cid = client.post("/api/campaigns/quick", json={"prompt": "Methanol"}).json()["campaign_id"]
    client.post("/api/zotero/connect", json={"method": "web", "api_key": "secret-zotero-key-123"})
    assert client.post(f"/api/zotero/push/{cid}", json={}).status_code == 400          # no collection for it
    zot.write = False
    zotero_store.set_maps("local", [{"library": "user", "collection_key": "COLA", "name": "M", "campaign_id": cid}])
    client.post(f"/api/campaigns/{cid}/literature/papers", json={"doi": KULD.doi})
    r = client.post(f"/api/zotero/push/{cid}", json={})
    assert r.status_code == 400 and "write" in r.json()["detail"]
