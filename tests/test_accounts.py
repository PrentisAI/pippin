"""Accounts, teams and project permissions: who may see and do what."""

import pytest
from fastapi.testclient import TestClient

from aurora.api.app import create_app
from aurora.auth import secrets as keys
from aurora.auth.guard import RULES
from aurora.auth.store import auth_store

PW = "correct horse battery"


@pytest.fixture()
def app():
    return create_app()


def client(app, email=None, password=PW):
    c = TestClient(app)
    if email:
        r = c.post("/api/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
    return c


@pytest.fixture()
def team(app):
    """An admin (the PI), two members and nobody signed in yet."""
    auth_store.add_user("wei@lab.example", "Wei Chen", PW, "admin")
    auth_store.add_user("linda@lab.example", "Linda Fei", PW, "member")
    auth_store.add_user("sara@lab.example", "Sara Novak", PW, "member")
    return {e.split("@")[0]: client(app, e) for e in ("wei@lab.example", "linda@lab.example", "sara@lab.example")}


def uid(email):
    return auth_store.user_by_email(email)["user_id"]


def test_without_accounts_nothing_asks_for_a_login(app):
    c = client(app)
    assert c.get("/api/auth/status").json() == {"accounts": False, "user": c.get("/api/auth/status").json()["user"], "team": None}
    assert c.get("/api/auth/status").json()["user"]["is_local"] is True
    cid = c.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
    assert c.get(f"/api/campaigns/{cid}").status_code == 200
    assert c.get(f"/api/campaigns/{cid}/access").json()["my_rights"][-1] == "manage"


def test_every_route_has_a_permission_rule(app):
    from fastapi.routing import APIRoute, APIWebSocketRoute
    open_routes = {"/api/health"}
    missing = []
    for r in app.routes:
        if isinstance(r, APIRoute) and r.path.startswith("/api/") and not r.path.startswith("/api/auth") and r.path not in open_routes:
            missing += [(m, r.path) for m in r.methods if (m, r.path) not in RULES and m != "HEAD"]
        if isinstance(r, APIWebSocketRoute) and ("WS", r.path) not in RULES:
            missing.append(("WS", r.path))
    assert missing == []


def test_sign_in_is_required_once_there_are_accounts(app, team):
    anon = client(app)
    assert anon.get("/api/campaigns/").status_code == 401
    assert anon.post("/api/auth/login", json={"email": "linda@lab.example", "password": "wrong password"}).status_code == 401
    assert team["linda"].get("/api/me").json()["user"]["name"] == "Linda Fei"
    team["linda"].post("/api/auth/logout")
    assert team["linda"].get("/api/campaigns/").status_code == 401


def test_a_project_is_private_to_its_members(app, team):
    linda, sara = team["linda"], team["sara"]
    cid = linda.post("/api/campaigns/quick", json={"prompt": "Citi Bike"}).json()["campaign_id"]
    assert [p["campaign_id"] for p in linda.get("/api/campaigns/").json()] == [cid]
    assert sara.get("/api/campaigns/").json() == []
    assert sara.get(f"/api/campaigns/{cid}").status_code == 404         # does not reveal that it exists
    assert team["wei"].get(f"/api/campaigns/{cid}").status_code == 404  # admins get no rights by being admins
    # shared as commenter: can look, cannot start runs, decide or share
    r = linda.put(f"/api/campaigns/{cid}/access/members/user:{uid('sara@lab.example')}", json={"role": "commenter"})
    assert r.status_code == 200
    assert sara.get(f"/api/campaigns/{cid}").status_code == 200
    assert sara.post(f"/api/campaigns/{cid}/experiments", json={"instruction": "x"}).status_code == 403
    assert sara.post(f"/api/campaigns/{cid}/access/invites", json={"email": "x@lab.example"}).status_code == 403
    assert sara.delete(f"/api/campaigns/{cid}").status_code == 403
    assert sara.get(f"/api/campaigns/{cid}/access").json()["my_rights"] == ["view", "comment"]


def test_custom_rights_and_nobody_hands_out_more_than_they_have(app, team):
    linda, sara = team["linda"], team["sara"]
    cid = linda.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
    sara_p = f"user:{uid('sara@lab.example')}"
    view = linda.put(f"/api/campaigns/{cid}/access/members/{sara_p}", json={"role": "custom", "perms": ["view", "comment", "share"]}).json()
    assert next(m for m in view["members"] if m["principal"] == sara_p)["perms"] == ["view", "comment", "share"]
    # Sara may share, but not give rights she lacks
    r = sara.post(f"/api/campaigns/{cid}/access/invites", json={"email": "tom@city.example", "role": "editor"})
    assert r.status_code == 403 and "run" in r.json()["detail"]
    r = sara.post(f"/api/campaigns/{cid}/access/invites", json={"email": "tom@city.example", "role": "commenter"})
    assert r.status_code == 200 and r.json()["team_role"] == "guest" and "/invite/" in r.json()["link"]
    # the last owner stays
    me = f"user:{uid('linda@lab.example')}"
    assert linda.put(f"/api/campaigns/{cid}/access/members/{me}", json={"role": "editor"}).status_code == 409
    assert linda.delete(f"/api/campaigns/{cid}/access/members/{me}").status_code == 409


def test_a_guest_from_another_institution(app, team):
    linda = team["linda"]
    cid = linda.post("/api/campaigns/quick", json={"prompt": "Citi Bike"}).json()["campaign_id"]
    other = linda.post("/api/campaigns/quick", json={"prompt": "Unpublished"}).json()["campaign_id"]
    linda.put(f"/api/campaigns/{other}/access", json={"team_access": "view"})
    r = linda.post(f"/api/campaigns/{cid}/access/invites", json={"email": "tom@city.example", "role": "editor"}).json()
    token = r["link"].rsplit("/", 1)[1]
    anon = client(app)
    info = anon.get(f"/api/auth/invites/{token}").json()
    assert info["project"] == "Citi Bike" and info["team_role"] == "guest" and info["invited_by"] == "Linda Fei"
    assert anon.post(f"/api/auth/invites/{token}/accept", json={"name": "Tom Ito", "password": "short"}).status_code == 400
    accepted = anon.post(f"/api/auth/invites/{token}/accept", json={"name": "Tom Ito", "password": PW})
    assert accepted.status_code == 200 and accepted.json()["user"]["team_role"] == "guest"
    tom = anon                                       # signed in by accepting
    assert [p["campaign_id"] for p in tom.get("/api/campaigns/").json()] == [cid]   # not the team-visible one
    assert tom.get(f"/api/campaigns/{other}").status_code == 404
    assert tom.post("/api/campaigns/quick", json={"prompt": "x"}).status_code == 403
    # editors may run, but guests may not unless the team allows it
    assert tom.post(f"/api/campaigns/{cid}/experiments", json={"instruction": "x"}).status_code == 403
    assert tom.get(f"/api/campaigns/{cid}/access").json()["my_rights"] == ["view", "comment", "edit", "decide"]
    assert anon.get(f"/api/auth/invites/{token}").status_code == 404           # used once


def test_team_access_and_confidential_projects(app, team):
    linda, sara = team["linda"], team["sara"]
    cid = linda.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
    linda.put(f"/api/campaigns/{cid}/access", json={"team_access": "comment"})
    assert sara.get(f"/api/campaigns/{cid}/access").json()["my_rights"] == ["view", "comment"]
    linda.put(f"/api/campaigns/{cid}/access", json={"confidential": True})
    assert sara.get(f"/api/campaigns/{cid}").status_code == 404


def test_groups_grant_access(app, team):
    linda, sara, wei = team["linda"], team["sara"], team["wei"]
    g = wei.post("/api/team/groups", json={"name": "Bike-share group", "members": [uid("sara@lab.example")]}).json()
    assert linda.post("/api/team/groups", json={"name": "x"}).status_code == 403     # admins only
    cid = linda.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
    linda.put(f"/api/campaigns/{cid}/access/members/group:{g['group_id']}", json={"role": "viewer"})
    assert sara.get(f"/api/campaigns/{cid}").status_code == 200


def test_team_admin_powers_and_limits(app, team):
    wei, linda = team["wei"], team["linda"]
    people = linda.get("/api/team/people").json()
    assert {p["name"] for p in people} == {"Wei Chen", "Linda Fei", "Sara Novak"} and "key" not in people[0]
    assert "key" in wei.get("/api/team/people").json()[0]
    assert wei.put(f"/api/team/people/{uid('wei@lab.example')}", json={"team_role": "member"}).status_code == 409
    assert wei.put("/api/team/rules", json={"guests_can_run": True}).json()["guests_can_run"] is True
    assert wei.put("/api/team/rules", json={"no_such_rule": 1}).status_code == 400
    # a project changes hands, e.g. when its owner leaves
    cid = linda.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
    assert wei.post("/api/team/transfer", json={"campaign_id": cid, "to_user_id": uid("sara@lab.example")}).status_code == 200
    assert team["sara"].get(f"/api/campaigns/{cid}/access").json()["my_rights"][-1] == "manage"
    log = wei.get("/api/team/activity").json()
    assert any("gave project" in e["action"] for e in log) and linda.get("/api/team/activity").status_code == 403


def test_api_keys_are_never_shown_back(app, team):
    linda, wei = team["linda"], team["wei"]
    r = linda.put("/api/me/keys/openrouter", json={"value": "sk-or-v1-abcdef1234567890"}).json()
    assert r["hint"] == "sk-or-…7890"
    assert "abcdef" not in linda.get("/api/me/keys").text and "abcdef" not in wei.get("/api/team/keys").text
    user = auth_store.as_user(auth_store.user_by_email("linda@lab.example"))
    assert keys.key_for(user) == ("sk-or-v1-abcdef1234567890", "own")
    wei.put("/api/team/keys/openrouter", json={"value": "sk-or-v1-team000000000000"})
    sara = auth_store.as_user(auth_store.user_by_email("sara@lab.example"))
    assert keys.key_for(sara) == ("sk-or-v1-team000000000000", "team")
    wei.put("/api/team/rules", json={"team_key_for_members": False})
    assert keys.key_for(sara) == ("", "none")


def test_search_and_analytics_show_only_what_you_may_see(app, team):
    linda, sara = team["linda"], team["sara"]
    linda.post("/api/campaigns/quick", json={"prompt": "Secret weekend demand study"})
    assert linda.get("/api/search", params={"q": "weekend"}).json()["results"].get("projects")
    assert not sara.get("/api/search", params={"q": "weekend"}).json()["results"].get("projects")


def test_websocket_needs_a_signed_in_member(app, team):
    from starlette.websockets import WebSocketDisconnect
    cid = team["linda"].post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
    with pytest.raises(WebSocketDisconnect):
        with client(app).websocket_connect(f"/api/campaigns/{cid}/experiments/x/ws") as ws:
            ws.receive_json()


def test_the_first_admin_adopts_existing_projects(tmp_path, monkeypatch):
    from aurora.cli import main
    c = TestClient(create_app())
    cid = c.post("/api/campaigns/quick", json={"prompt": "made before accounts"}).json()["campaign_id"]
    with pytest.raises(SystemExit) as e:
        main(["users", "add", "pi@lab.example", "--name", "PI", "--password", PW])
    assert e.value.code == 1                         # the first account must be an admin
    with pytest.raises(SystemExit) as e:
        main(["users", "add", "pi@lab.example", "--name", "PI", "--admin", "--password", PW])
    assert e.value.code == 0
    pi = client(create_app(), "pi@lab.example")
    assert [p["campaign_id"] for p in pi.get("/api/campaigns/").json()] == [cid]


def test_each_person_has_their_own_conversation_and_key(app, team):
    linda, sara, wei = team["linda"], team["sara"], team["wei"]
    cid = linda.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
    linda.put(f"/api/campaigns/{cid}/access", json={"team_access": "comment"})
    assert [m["text"] for m in linda.get(f"/api/campaigns/{cid}/chat/history").json()] == ["Project created"]
    assert sara.get(f"/api/campaigns/{cid}/chat/history").json() == []      # not Linda's conversation
    # no key of her own and no team key: told so, never the server's key behind her back
    wei.put("/api/team/rules", json={"team_key_for_members": False})
    r = sara.post(f"/api/campaigns/{cid}/chat/send", json={"text": "hi"})
    assert r.status_code == 400 and "API key" in r.json()["detail"]


def test_a_run_records_who_started_it_and_uses_their_key(app, team):
    import time

    from aurora.api.routes import campaigns as routes
    linda = team["linda"]
    linda.put("/api/me/keys/openrouter", json={"value": "sk-or-v1-linda0000000000"})
    cid = linda.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
    r = linda.post(f"/api/campaigns/{cid}/experiments", json={"instruction": "Zn segregation", "mock_mode": True})
    assert r.status_code == 200, r.text
    eid = r.json()["experiment_id"]
    run = routes._get_run(cid, eid)
    assert run.engine._config.api_key == "sk-or-v1-linda0000000000"
    assert linda.get(f"/api/campaigns/{cid}/experiments/{eid}").json()["started_by"] == "Linda Fei"
    for _ in range(200):
        if run.status in ("completed", "failed", "stopped"):
            break
        time.sleep(0.05)


def test_who_may_answer_decisions(app, team):
    from aurora.api.routes import campaigns as routes
    from aurora.auth.guard import CURRENT
    linda = team["linda"]
    cid = linda.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
    linda.put(f"/api/campaigns/{cid}/access/members/user:{uid('sara@lab.example')}", json={"role": "editor"})
    run = routes.ExperimentRunState("r1", cid, "r", "m")
    run.started_by = uid("sara@lab.example")
    as_ = lambda email: auth_store.as_user(auth_store.user_by_email(email))   # noqa: E731

    def check(email):
        token = CURRENT.set(as_(email))
        try:
            return routes.may_decide(cid, run)
        finally:
            CURRENT.reset(token)
    assert check("sara@lab.example") == (True, "Sara Novak")            # she started it
    assert check("linda@lab.example")[0] is True                        # owners can step in
    linda.put(f"/api/campaigns/{cid}/access", json={"deciders": "owners"})
    assert check("sara@lab.example")[0] is False
    linda.put(f"/api/campaigns/{cid}/access", json={"deciders": "user", "decider_user": uid("sara@lab.example")})
    assert check("sara@lab.example")[0] is True and check("linda@lab.example")[0] is False
    r = linda.put(f"/api/campaigns/{cid}/access", json={"deciders": "user", "decider_user": uid("wei@lab.example")})
    assert r.status_code == 400                                          # Wei cannot decide there


def test_notification_channels_are_personal(app, team):
    linda, sara = team["linda"], team["sara"]
    r = linda.put("/api/settings/notifications", json={"channels": [{"type": "serverchan", "target": "SCT1234567890abcd"}]})
    assert r.status_code == 200 and r.json()["channels"][0]["target_hint"].endswith("abcd")
    assert sara.get("/api/settings/notifications").json()["channels"] == []
    assert linda.put("/api/settings", json={"changes": {"budget.max_hours": 3}}).status_code == 403   # server defaults: admins


def test_the_project_list_says_how_each_project_is_shared(app, team):
    linda, sara = team["linda"], team["sara"]
    cid = linda.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
    s = linda.get("/api/campaigns/").json()[0]["sharing"]
    assert s["members"] == 1 and s["team_access"] == "none" and "manage" in s["my_rights"]
    linda.put(f"/api/campaigns/{cid}/access/members/user:{uid('sara@lab.example')}", json={"role": "viewer"})
    s = sara.get(f"/api/campaigns/{cid}").json()["sharing"]
    assert s["members"] == 2 and s["my_rights"] == ["view"]
    assert client(create_app()).get("/api/campaigns/").status_code == 401


def test_without_accounts_the_project_list_has_no_sharing(app):
    c = client(app)
    c.post("/api/campaigns/quick", json={"prompt": "p"})
    assert c.get("/api/campaigns/").json()[0]["sharing"] is None
