"""Visitor authorization, idempotency, publication and payment boundaries."""
import io
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from PIL import Image

PREFIX = "/api/v1/scenic/visitor"
HEADERS = {"X-Visitor-Request": "1"}


def test_new_scenic_project_is_open_and_saved_disable_survives_startup(client, register_scenic_user):
    import scenic_database as d
    from app.services.visitor_configuration import initialize_missing_visitor_services
    user = register_scenic_user()
    pid = client.post("/api/v1/scenic/projects", headers=user["headers"], json={"name": "新景区"}).json()["id"]
    response = client.get(f"{PREFIX}/sites/{pid}")
    assert response.status_code == 200
    assert response.json()["name"] == "新景区"
    with d.SessionLocal() as db:
        db.get(d.VisitorService, pid).enabled = False
        db.commit()
    assert initialize_missing_visitor_services() == 0
    assert client.get(f"{PREFIX}/sites/{pid}").status_code == 404
    # Earlier releases created projects without a visitor configuration.
    with d.SessionLocal() as db:
        db.delete(db.get(d.VisitorService, pid))
        db.commit()
    assert initialize_missing_visitor_services() == 1
    assert initialize_missing_visitor_services() == 0
    assert client.get(f"{PREFIX}/sites/{pid}").status_code == 200


@pytest.fixture
def setup_visitor(client, register_scenic_user, monkeypatch):
    import scenic_database as d
    from app.services.time_utils import utc_now
    from app.services.visitor_auth import COOKIE, digest
    from app.core.config import get_settings
    from app.api.scenic import visitor_routes
    user = register_scenic_user()
    pid = client.post("/api/v1/scenic/projects", headers=user["headers"], json={"name": "青山景区"}).json()["id"]
    monkeypatch.setattr(get_settings(), "visitor_cookie_secure", False)
    monkeypatch.setattr(get_settings(), "visitor_public_base_url", "https://visitor.example.com")
    monkeypatch.setattr(get_settings(), "wechat_pay_appid", "visitor-app")
    monkeypatch.setattr(visitor_routes, "verify_reference", lambda image, box: None)
    with d.SessionLocal() as db:
        config = db.get(d.VisitorService, pid)
        config.enabled = True
        config.price_cents = 1990
        account = d.VisitorAccount(appid="visitor-app", openid="visitor-one")
        other = d.VisitorAccount(appid="visitor-app", openid="visitor-two")
        db.add_all([config, account, other]); db.flush()
        db.add(d.VisitorSession(token_hash=digest("one"), account_id=account.id, expires_at=utc_now()+timedelta(days=1)))
        db.add(d.VisitorSession(token_hash=digest("two"), account_id=other.id, expires_at=utc_now()+timedelta(days=1)))
        db.commit()
    client.cookies.set(COOKIE, "one")
    return pid, user


def register(client, pid, key="registration-key-1", **overrides):
    buffer = io.BytesIO(); Image.new("RGB", (120,240), "green").save(buffer, "JPEG")
    data = {"request_key":key, "consent_granted":"true", "consent_version":"visitor-v1", "target_box":"[0,0,1,1]", **overrides}
    return client.post(f"{PREFIX}/sites/{pid}/visits", headers=HEADERS, data=data,
        files={"file":("photo.jpg",buffer.getvalue(),"image/jpeg")})


def ready(client, pid):
    import scenic_database as d
    from app.services.storage import storage
    response=register(client,pid)
    assert response.status_code==200, response.text
    vid=response.json()["id"]
    with d.SessionLocal() as db:
        row=db.get(d.VisitorVisit,vid)
        vlog=d.ScenicVlogJob(session_id=row.session_id,created_by_username=row.session.created_by_username,status="succeeded")
        db.add(vlog);db.flush()
        for orientation in ("landscape","portrait"):
            full=storage.save_upload(f"scenic/{pid}",orientation+"-full.mp4",io.BytesIO(b"full-video"),category="tests")
            preview=storage.save_upload(f"scenic/{pid}",orientation+"-preview.mp4",io.BytesIO(b"preview-video"),category="tests")
            db.add(d.ScenicVlogVariant(vlog_job_id=vlog.id,orientation=orientation,status="succeeded",duration_seconds=10,
                full_object_key=full.object_key,preview_object_key=preview.object_key))
        row.vlog_id,row.stage=vlog.id,"ready"
        db.commit()
    return vid


def test_registration_idempotency_consent_and_private_dto(client, setup_visitor):
    import scenic_database as d
    pid,_=setup_visitor
    assert register(client,pid,consent_granted="false").status_code==422
    assert register(client,pid,consent_version="old").status_code==422
    first=register(client,pid);second=register(client,pid)
    assert first.status_code==200,first.text
    assert first.json()["id"]==second.json()["id"]
    assert not {"openid","entry_image_object_key","internal_error","segments","payment_code_url"} & first.json().keys()
    with d.SessionLocal() as db:
        assert db.query(d.TouristSession).count()==1
        assert db.query(d.VisitorVisit).count()==1
        assert db.query(d.VisitorVisit).one().consent_text


def test_ownership_staff_token_and_csrf(client, setup_visitor):
    from app.services.visitor_auth import COOKIE
    pid,user=setup_visitor
    vid=register(client,pid).json()["id"]
    assert client.post(f"{PREFIX}/visits/{vid}/finish").status_code==403
    assert client.post(f"{PREFIX}/visits/{vid}/finish",headers={**HEADERS,"Origin":"https://evil.example"}).status_code==403
    client.cookies.set(COOKIE,"two")
    for path in (f"/visits/{vid}",f"/visits/{vid}/video/landscape/preview"):
        assert client.get(PREFIX+path).status_code==404
    for path in ("finish","pay","payment-status","claim"):
        assert client.post(f"{PREFIX}/visits/{vid}/{path}",headers=HEADERS).status_code==404
    assert client.get(PREFIX+"/visits").json()=={"visits":[]}
    client.cookies.clear()
    assert client.get(PREFIX+"/me",headers=user["headers"]).status_code==401


def test_finish_is_durable_and_idempotent(client, setup_visitor, monkeypatch):
    import scenic_database as d
    pid,_=setup_visitor
    vid=register(client,pid).json()["id"]
    from app.services import camera_stream
    calls=[]
    monkeypatch.setattr(camera_stream, 'solidify_project_camera_recordings', lambda *args:calls.append(True))
    a=client.post(f"{PREFIX}/visits/{vid}/finish",headers=HEADERS)
    b=client.post(f"{PREFIX}/visits/{vid}/finish",headers=HEADERS)
    assert a.status_code==200,a.text
    assert a.json()["stage"]==b.json()["stage"]=="sealing"
    assert a.json()["checked_out_at"]==b.json()["checked_out_at"]
    with d.SessionLocal() as db:
        assert db.get(d.VisitorVisit,vid).requested_at is not None
    assert not calls
    from app.services.visitor_production import prepare_visit_recordings
    prepare_visit_recordings(vid)
    prepare_visit_recordings(vid)
    assert calls==[True]
    assert client.get(f'{PREFIX}/visits/{vid}').json()['stage']=='waiting'


def test_visitor_path_id_is_bound_as_integer_for_postgres(client, setup_visitor):
    """SQLite coerces string IDs; assert the PostgreSQL binding at the shared guard."""
    import scenic_database as d
    from sqlalchemy import event, Integer
    from sqlalchemy.dialects import postgresql
    pid, _ = setup_visitor
    vid = register(client, pid).json()["id"]
    checked = []

    def inspect_binding(conn, clause, multiparams, params, options):
        compiled = clause.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        if "SELECT tourist_sessions.project_id" in sql and "visitor_visits.id =" in sql:
            assert isinstance(compiled.params["id_1"], int)
            assert isinstance(compiled.binds["id_1"].type, Integer)
            checked.append(compiled.params["id_1"])

    event.listen(d.engine, "before_execute", inspect_binding)
    try:
        assert client.post(f"{PREFIX}/visits/{vid}/finish", headers=HEADERS).status_code == 200
        assert client.post(f"{PREFIX}/visits/{vid}/finish", headers=HEADERS).status_code == 200
        for action in ("retry", "pay", "payment-status", "claim", "render"):
            response = client.post(f"{PREFIX}/visits/{vid}/{action}", headers=HEADERS, json={"revision": 1})
            assert response.status_code < 500, response.text
        assert client.get(f"{PREFIX}/visits/{vid}/segments/999/preview").status_code == 404
        assert client.post(f"{PREFIX}/visits/{vid + 1000}/finish", headers=HEADERS).status_code == 404
        assert client.post(f"{PREFIX}/visits/not-a-number/finish", headers=HEADERS).status_code == 422
    finally:
        event.remove(d.engine, "before_execute", inspect_binding)
    assert len(checked) >= 9


def test_accepted_finish_does_not_wait_for_busy_sealing_lock(client, setup_visitor):
    from filelock import FileLock
    from app.workers.scenic_editing import lock_path
    pid,_=setup_visitor
    vid=register(client,pid).json()['id']
    assert client.post(f'{PREFIX}/visits/{vid}/finish',headers=HEADERS).status_code==200
    with FileLock(lock_path(f'visitor-{vid}')):
        response=client.post(f'{PREFIX}/visits/{vid}/finish',headers=HEADERS)
    assert response.status_code==200
    assert response.json()['stage']=='sealing'


@pytest.mark.parametrize('within,sealed', [(True,False),(False,False),(True,True)])
def test_waiting_only_depends_on_unavailable_recordings_within_this_visit(client, setup_visitor, monkeypatch, within, sealed):
    import scenic_database as d
    from app.services.time_utils import utc_now
    from app.services.visitor_production import production_step
    from app.api.scenic import tourist_routes
    pid,_=setup_visitor
    vid=register(client,pid).json()['id']
    now=utc_now()
    calls=[]
    monkeypatch.setattr(tourist_routes,'v1_match_tourist_segments',lambda *args:calls.append(True))
    with d.SessionLocal() as db:
        row=db.get(d.VisitorVisit,vid)
        row.stage='waiting';row.requested_at=now
        row.session.created_at=now-timedelta(minutes=2);row.session.checked_out_at=now-timedelta(minutes=1)
        media=d.MediaFile(project_id=pid,uploader_username=row.session.created_by_username,filename='part.avi',object_key='part.avi',media_type='camera')
        db.add(media);db.flush()
        if sealed:
            from app.core.config import get_settings
            path=get_settings().object_storage_dir/media.object_key
            path.write_bytes(b'sealed-video')
            path.with_suffix('.avi.sealed').write_text(str(path.stat().st_size))
        timestamp=now-timedelta(seconds=90) if within else now
        db.add(d.DetectionTarget(project_id=pid,media_id=media.id,crop_path='crop.jpg',vector_id='point',timestamp=timestamp.isoformat()))
        db.add(d.ProcessingJob(id='publish',project_id=pid,media_id=media.id,job_type='camera_recording',status='queued'))
        db.commit()
    production_step(vid)
    assert calls==([] if within and not sealed else [True])


def test_unpaid_video_and_version_ownership(client, setup_visitor):
    pid,_=setup_visitor
    vid=ready(client,pid)
    assert client.get(f"{PREFIX}/visits/{vid}/video/landscape/full").status_code==402
    assert client.get(f"{PREFIX}/visits/{vid}/video/landscape/preview").content==b"preview-video"
    assert client.get(f"{PREFIX}/visits/{vid}/video/landscape/draft-preview").status_code==404
    assert client.post(f"{PREFIX}/visits/{vid}/claim",headers=HEADERS).status_code==403


def test_jsapi_idempotent_order_server_price_and_paid_delivery(client, setup_visitor, monkeypatch):
    import scenic_database as d
    from app.services.payments import WechatNativePaymentClient, WechatPaymentStatus
    from app.services.time_utils import utc_now
    pid,user=setup_visitor
    vid=ready(client,pid)
    calls=[]
    monkeypatch.setattr(WechatNativePaymentClient,"create_jsapi_order",lambda self,request,openid:calls.append((request,openid)) or "prepay-one")
    monkeypatch.setattr(WechatNativePaymentClient,"jsapi_parameters",lambda self,value:{"package":"prepay_id="+value})
    monkeypatch.setattr(WechatNativePaymentClient,"query_order",lambda self,no:WechatPaymentStatus(no,"NOTPAY",amount_cents=1990,currency="CNY"))
    first=client.post(f"{PREFIX}/visits/{vid}/pay",headers=HEADERS,json={"price_cents":1,"openid":"attacker"})
    second=client.post(f"{PREFIX}/visits/{vid}/pay",headers=HEADERS)
    assert first.status_code==200,first.text
    assert first.json()["visit"]["order"]["id"]==second.json()["visit"]["order"]["id"]
    assert len(calls)==1 and calls[0][0].amount_cents==1990 and calls[0][1]=="visitor-one"
    monkeypatch.setattr(WechatNativePaymentClient,"query_order",lambda self,no:WechatPaymentStatus(no,"SUCCESS",transaction_id="wx-trade",amount_cents=1990,currency="CNY"))
    assert client.post(f"{PREFIX}/visits/{vid}/payment-status",headers=HEADERS).json()["order"]["status"]=="paid"
    assert client.get(f"{PREFIX}/visits/{vid}/video/portrait/full?download=true").content==b"full-video"
    download_url=client.get(f"{PREFIX}/visits/{vid}").json()["download_urls"]["portrait"]
    from app.services.visitor_auth import COOKIE
    client.cookies.clear()
    assert client.get(download_url).content==b"full-video"
    assert client.get(download_url.replace("/portrait?","/landscape?")).status_code==403
    assert client.get(download_url.split("?")[0]).status_code==403
    client.cookies.set(COOKIE,"one")
    assert client.post(f"{PREFIX}/visits/{vid}/pay",headers=HEADERS).json()["payment"] is None
    assert len(calls)==1
    assert client.post(f"{PREFIX}/visits/{vid}/claim",headers=HEADERS).json()["order"]["claimed"]
    assert client.post(f"/api/v1/scenic/visitor-requests/{vid}/resume",headers=user["headers"],json={"action":"retry"}).status_code==409
    with d.SessionLocal() as db:
        row=db.get(d.VisitorVisit,vid)
        row.order.paid_at=utc_now()-timedelta(days=366);db.commit()
    assert client.get(f"{PREFIX}/visits/{vid}/video/portrait/full").status_code==410
    assert client.get(download_url).status_code==410


def test_payment_wrong_amount_does_not_unlock(client, setup_visitor, monkeypatch):
    import scenic_database as d
    from app.services.payments import WechatNativePaymentClient, WechatPaymentStatus
    pid,_=setup_visitor; vid=ready(client,pid)
    monkeypatch.setattr(WechatNativePaymentClient,"create_jsapi_order",lambda *args:"p")
    monkeypatch.setattr(WechatNativePaymentClient,"jsapi_parameters",lambda *args:{})
    assert client.post(f"{PREFIX}/visits/{vid}/pay",headers=HEADERS).status_code==200
    monkeypatch.setattr(WechatNativePaymentClient,"query_order",lambda self,no:WechatPaymentStatus(no,"SUCCESS",amount_cents=1,currency="CNY"))
    assert client.post(f"{PREFIX}/visits/{vid}/payment-status",headers=HEADERS).status_code==503
    assert client.get(f"{PREFIX}/visits/{vid}/video/portrait/full").status_code==402
    with d.SessionLocal() as db:
        assert db.query(d.ScenicOrder).count()==1


def test_service_staff_permissions_and_site_closed(client, setup_visitor, register_scenic_user):
    import scenic_database as d
    pid,user=setup_visitor
    stranger=register_scenic_user()
    assert client.get(f"/api/v1/scenic/projects/{pid}/visitor-service",headers=stranger["headers"]).status_code in (403,404)
    assert client.get(f"/api/v1/scenic/projects/{pid}/visitor-queue",headers=stranger["headers"]).status_code in (403,404)
    with d.SessionLocal() as db:
        db.get(d.VisitorService,pid).enabled=False;db.commit()
    assert client.get(f"{PREFIX}/sites/{pid}").status_code==404
    assert register(client,pid).status_code==404


def test_staff_identity_verification_gates_visitor_media(client, setup_visitor, register_scenic_user, monkeypatch):
    from app.core.config import get_settings
    import scenic_database as d
    pid, user = setup_visitor
    monkeypatch.setattr(get_settings(), "visitor_require_staff_verification", True)
    vid = ready(client, pid)
    url = f"{PREFIX}/visits/{vid}/video/portrait/preview"
    assert client.get(url).status_code == 403
    assert client.get(f"{PREFIX}/visits/{vid}").json()["video_available"] is False
    stranger = register_scenic_user()
    action = f"/api/v1/scenic/visitor-requests/{vid}/resume"
    assert client.post(action, headers=stranger["headers"], json={"action": "verify"}).status_code in (403, 404)
    assert client.post(action, headers=user["headers"], json={"action": "verify"}).status_code == 200
    assert client.get(url).content == b"preview-video"
    with d.SessionLocal() as db:
        row = db.get(d.VisitorVisit, vid)
        assert row.verified_at and row.verified_by == user["username"]
        assert db.query(d.AuditLog).filter_by(action="visitor.identity_verified").count() == 1


def test_unknown_cross_date_and_cross_project_capture_rejected(client, setup_visitor):
    from app.services.visitor_production import within_visit
    from app.services.time_utils import utc_now
    now=utc_now()
    session=SimpleNamespace(project_id=1,created_at=now,checked_out_at=now+timedelta(hours=2))
    media=SimpleNamespace(project_id=1,media_type="video",scenic_metadata=None)
    target=SimpleNamespace(media_file=media)
    assert not within_visit(target,session,0,8)
    media.scenic_metadata=SimpleNamespace(recorded_at=now-timedelta(days=1))
    assert not within_visit(target,session,0,8)
    media.scenic_metadata.recorded_at=now+timedelta(minutes=5)
    assert within_visit(target,session,0,8)
    media.project_id=2
    assert not within_visit(target,session,0,8)


def test_partial_render_and_missing_assets_need_review(client, setup_visitor):
    import scenic_database as d
    from app.services.visitor_production import production_step, output_complete
    from app.services.time_utils import utc_now
    pid,_=setup_visitor;vid=ready(client,pid)
    with d.SessionLocal() as db:
        row=db.get(d.VisitorVisit,vid)
        assert output_complete(row.vlog)
        row.stage="rendering";row.requested_at=utc_now();row.vlog.status="partial"
        row.vlog.variants[0].status="failed";db.commit()
    production_step(vid)
    assert client.get(f"{PREFIX}/visits/{vid}").json()["stage"]=="review"
    assert client.get(f"{PREFIX}/visits/{vid}/video/portrait/preview").status_code==404


def test_no_material_and_coordinator_restart(client, setup_visitor, monkeypatch):
    import scenic_database as d
    from app.services.visitor_production import production_step
    from app.services.time_utils import utc_now
    from app.api.scenic import tourist_routes
    pid,_=setup_visitor;vid=register(client,pid).json()["id"]
    monkeypatch.setattr(tourist_routes,"v1_match_tourist_segments",lambda *args:None)
    with d.SessionLocal() as db:
        row=db.get(d.VisitorVisit,vid);row.stage="matching";row.requested_at=utc_now();db.commit()
    production_step(vid);production_step(vid)
    with d.SessionLocal() as db:
        row=db.get(d.VisitorVisit,vid)
        assert row.stage=="review" and row.reason=="no_material"


def test_oauth_state_forgery_is_rejected(client, setup_visitor):
    client.cookies.set("visitor_oauth","forged.invalid")
    assert client.get(PREFIX+"/auth/callback?state=forged&code=x").status_code==400


def test_jsapi_signature_uses_official_newline_format(client, setup_visitor, monkeypatch):
    from app.services.payments import WechatNativePaymentClient
    client=WechatNativePaymentClient();messages=[]
    monkeypatch.setattr(client,"_sign",lambda value:messages.append(value) or "signature")
    result=client.jsapi_parameters("prepay")
    assert messages==[f"{result['appId']}\n{result['timeStamp']}\n{result['nonceStr']}\nprepay_id=prepay\n".encode()]
    assert result["signType"]=="RSA"


def test_fake_payment_callback_cannot_unlock_jsapi(client, setup_visitor, monkeypatch):
    from app.services.payments import WechatNativePaymentClient
    pid,_=setup_visitor;vid=ready(client,pid)
    monkeypatch.setattr(WechatNativePaymentClient,"create_jsapi_order",lambda *args:"p")
    monkeypatch.setattr(WechatNativePaymentClient,"jsapi_parameters",lambda *args:{})
    data=client.post(f"{PREFIX}/visits/{vid}/pay",headers=HEADERS).json()
    body={"out_trade_no":data["visit"]["order"]["order_no"],"trade_state":"SUCCESS","amount":{"total":1990,"currency":"CNY"}}
    for endpoint in ("/api/v1/scenic/payments/wechat/notify",PREFIX+"/payments/wechat/notify"):
        response=client.post(endpoint,headers={"Wechatpay-Signature":"VALID"},json=body)
        assert response.status_code==400,response.text
    assert client.get(f"{PREFIX}/visits/{vid}/video/landscape/full").status_code==402


def test_legacy_planning_request_returns_to_visitor_selection_without_staff_or_auto_submit(client, setup_visitor, monkeypatch):
    import scenic_database as d
    from app.core.config import get_settings
    from app.api.scenic import editing_routes
    from app.services.time_utils import utc_now
    from app.services.visitor_production import production_step
    pid,_=setup_visitor;vid=ready(client,pid)
    now=utc_now()
    monkeypatch.setattr(get_settings(),"visitor_auto_publish",True)
    with d.SessionLocal() as db:
        row=db.get(d.VisitorVisit,vid)
        row.session.created_at=now-timedelta(hours=1);row.session.checked_out_at=now
        row.stage="planning";row.requested_at=now
        media=d.MediaFile(project_id=pid,uploader_username=row.session.created_by_username,filename="camera.mp4",
            object_key="tests/camera.mp4",media_type="camera",created_at=now-timedelta(minutes=10))
        db.add(media);db.flush()
        clip={"media_id":media.id,"start_seconds":0,"end_seconds":8,"trajectory":[{"x":0.5,"y":0.5}],"identity_similarity":0.99}
        draft=d.ScenicEditDraft(session_id=row.session_id,revision=1,data_json=json.dumps({"variants":{"landscape":[clip],"portrait":[clip]}}))
        db.add(draft);db.flush();row.draft_id=draft.id
        vlog_id=row.vlog_id;db.commit()
    submissions=[]
    monkeypatch.setattr(editing_routes,"submit_edited_vlog",lambda sid,payload,actor,db:submissions.append(payload.draft_id) or {"vlog":{"id":vlog_id}})
    production_step(vid);production_step(vid)
    with d.SessionLocal() as db:
        assert db.get(d.VisitorVisit,vid).stage=="selecting"
        assert db.query(d.ScenicEditDraft).count()==1
    assert not submissions


@pytest.mark.parametrize("problem",["no_identity","other_date","too_short","other_project"])
def test_automatic_publish_rejects_unsafe_planned_clips(client, setup_visitor, problem):
    from app.services.visitor_production import safe_to_publish
    from app.services.time_utils import utc_now
    now=utc_now()
    session=SimpleNamespace(project_id=1,created_at=now-timedelta(hours=1),checked_out_at=now)
    media=SimpleNamespace(project_id=1,media_type="camera",created_at=now-timedelta(minutes=5),scenic_metadata=None)
    clip={"media_id":1,"start_seconds":0,"end_seconds":8,"trajectory":[1],"identity_similarity":0.99}
    if problem=="no_identity":clip["trajectory"]=[]
    if problem=="other_date":media.created_at=now-timedelta(days=1)
    if problem=="too_short":clip["end_seconds"]=0.5
    if problem=="other_project":media.project_id=2
    db=SimpleNamespace(get=lambda *args:media)
    assert not safe_to_publish(db,session,{"variants":{"landscape":[clip],"portrait":[clip]}})


@pytest.mark.parametrize("people",[[],[object(),object()]])
def test_reference_photo_rejects_missing_or_multiple_people(monkeypatch, people):
    from app.api.scenic.visitor_routes import verify_reference
    from app.services import model_runtime
    from fastapi import HTTPException
    monkeypatch.setattr(model_runtime,"detect_people",lambda *args,**kwargs:people)
    with pytest.raises(HTTPException) as error:
        verify_reference(Image.new("RGB",(120,240)),[0,0,1,1])
    assert error.value.status_code==422


@pytest.fixture
def visitor_testing(setup_visitor, monkeypatch):
    from app.core.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "visitor_auth_provider", "test")
    monkeypatch.setattr(settings, "visitor_payment_provider", "test")
    monkeypatch.setattr(settings, "visitor_test_invite_code", "integration-invite-secret")
    return setup_visitor


def test_browser_login_reuses_identity_and_isolates_browsers(client, setup_visitor, monkeypatch):
    import scenic_database as d
    from app.core.config import get_settings
    from app.services.visitor_auth import COOKIE, digest
    pid, _ = setup_visitor
    monkeypatch.setattr(get_settings(), "visitor_auth_provider", "browser")
    client.cookies.clear()
    payload = {"project_id": pid}
    assert client.post(PREFIX + "/auth/browser", json=payload).status_code == 403
    assert client.post(PREFIX + "/auth/browser", headers={**HEADERS, "Origin": "https://evil.example"}, json=payload).status_code == 403
    response = client.post(PREFIX + "/auth/browser", headers=HEADERS, json=payload)
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    token = client.cookies.get(COOKIE)
    vid = register(client, pid).json()["id"]
    assert client.post(PREFIX + "/auth/browser", headers=HEADERS, json=payload).status_code == 200
    assert client.cookies.get(COOKIE) == token
    assert client.get(f"{PREFIX}/visits/{vid}").status_code == 200
    with d.SessionLocal() as db:
        assert db.query(d.VisitorAccount).filter_by(appid="visitor-browser").count() == 1
        db.get(d.VisitorService, pid).enabled = False
        db.commit()
    assert client.post(PREFIX + "/auth/browser", headers=HEADERS, json=payload).status_code == 404
    with d.SessionLocal() as db:
        db.get(d.VisitorService, pid).enabled = True
        db.commit()
    client.cookies.clear()
    assert client.post(PREFIX + "/auth/browser", headers=HEADERS, json=payload).status_code == 200
    assert client.cookies.get(COOKIE) != token
    assert client.get(PREFIX + "/visits").json() == {"visits": []}
    assert client.get(f"{PREFIX}/visits/{vid}").status_code == 404
    with d.SessionLocal() as db:
        db.get(d.VisitorSession, digest(client.cookies.get(COOKIE))).expires_at -= timedelta(days=31)
        db.commit()
    assert client.get(PREFIX + "/me").status_code == 401
    assert client.post(PREFIX + "/auth/browser", headers=HEADERS, json=payload).status_code == 200
    monkeypatch.setattr(get_settings(), "visitor_auth_provider", "wechat")
    assert client.post(PREFIX + "/auth/browser", headers=HEADERS, json=payload).status_code == 200
    assert client.get(PREFIX + "/me").json()["auth_provider"] == "browser"
    monkeypatch.setattr(get_settings(), "visitor_auth_provider", "test")
    assert client.post(PREFIX + "/auth/browser", headers=HEADERS, json=payload).status_code == 404
    assert client.get(PREFIX + "/me").status_code != 200


@pytest.mark.parametrize("environment", ["production", "prod", "staging", "local"])
@pytest.mark.parametrize("provider", ["browser", "wechat"])
def test_browser_login_and_selfservice_in_deployed_environments(client, setup_visitor, monkeypatch, environment, provider):
    from app.core.config import get_settings, Settings
    from app.services.rate_limit import rate_limiter
    # No Redis runs in this isolated suite; production correctly fails closed
    # without it. This test targets authentication and self-service gates.
    monkeypatch.setattr(rate_limiter, "enforce", lambda *args: None)
    settings = get_settings()
    monkeypatch.setattr(settings, "visitor_auth_provider", provider)
    monkeypatch.setattr(settings, "app_env", environment)
    # Verify the code default, independently of conftest's environment override.
    assert Settings.model_fields["visitor_require_staff_verification"].default is False
    monkeypatch.setattr(settings, "visitor_require_staff_verification", False)
    client.cookies.clear()
    assert client.get(PREFIX + "/runtime").json()["browser_login_available"] is True
    pid, _ = setup_visitor
    response = client.post(PREFIX + "/auth/browser", headers=HEADERS, json={"project_id": pid})
    assert response.status_code == 200
    assert response.json()["auth_provider"] == "browser"
    registration = register(client, pid)
    assert registration.status_code == 200, registration.text
    visit = registration.json()
    result = client.post(f"{PREFIX}/visits/{visit['id']}/finish", headers=HEADERS)
    assert result.status_code == 200
    assert result.json()["stage"] == "sealing"
    assert result.json()["identity_verified"] is False


def test_test_login_isolation_and_request_guards(client, visitor_testing):
    pid, _ = visitor_testing
    payload = {"project_id": pid, "visitor": "A", "invite_code": "integration-invite-secret"}
    client.cookies.clear()
    assert client.post(PREFIX + "/auth/test", json=payload).status_code == 403
    assert client.post(PREFIX + "/auth/test", headers={**HEADERS, "Origin": "https://evil.example"}, json=payload).status_code == 403
    assert client.post(PREFIX + "/auth/test", headers=HEADERS, json={**payload, "invite_code": "wrong"}).status_code == 403
    assert client.post(PREFIX + "/auth/test", headers=HEADERS, json=payload).status_code == 200
    vid = register(client, pid).json()["id"]
    assert client.post(PREFIX + "/auth/test", headers=HEADERS, json={**payload, "visitor": "B"}).status_code == 200
    assert client.get(PREFIX + "/visits").json() == {"visits": []}
    assert client.get(f"{PREFIX}/visits/{vid}").status_code == 404
    assert client.post(PREFIX + "/auth/test", headers=HEADERS, json=payload).status_code == 200
    assert client.get(f"{PREFIX}/visits/{vid}").status_code == 200


def test_test_checkout_all_outcomes_and_delivery(client, visitor_testing):
    import scenic_database as d
    from app.services.time_utils import utc_now
    from app.services.visitor_auth import COOKIE
    pid, _ = visitor_testing
    vid = ready(client, pid)
    result = client.post(f"{PREFIX}/visits/{vid}/pay", headers=HEADERS, json={"amount_cents": 1}).json()
    assert result["payment"]["provider"] == "test"
    assert result["payment"]["amount_cents"] == 1990
    token = result["payment"]["token"]
    def send(outcome, value=token):
        return client.post(f"{PREFIX}/visits/{vid}/test-payment", headers=HEADERS, json={"outcome": outcome, "token": value})
    assert send("success", "forged").status_code == 403
    assert send("success", "无效凭据").status_code == 403
    client.cookies.set(COOKIE, "two")
    assert send("success").status_code == 404
    client.cookies.set(COOKIE, "one")
    assert send("duplicate").status_code == 409
    assert send("cancel").json()["order"]["status"] == "pending"
    assert send("failure").json()["order"]["status"] != "paid"
    assert client.get(f"{PREFIX}/visits/{vid}/video/portrait/full").status_code == 402
    assert send("delayed").json()["order"]["status"] == "pending"
    assert send("cancel").status_code == 409
    assert client.post(f"{PREFIX}/visits/{vid}/payment-status", headers=HEADERS).json()["order"]["status"] == "pending"
    # Simulate elapsed time using persisted state, independent of process memory.
    with d.SessionLocal() as db:
        row = db.get(d.VisitorVisit, vid)
        row.order.payment_expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
    assert client.post(f"{PREFIX}/visits/{vid}/payment-status", headers=HEADERS).json()["order"]["status"] == "paid"
    with d.SessionLocal() as db:
        paid_at = db.get(d.VisitorVisit, vid).order.paid_at
    assert send("duplicate").json()["order"]["status"] == "paid"
    assert send("failure").json()["order"]["status"] == "paid"
    with d.SessionLocal() as db:
        assert db.get(d.VisitorVisit, vid).order.paid_at == paid_at
        assert db.query(d.ScenicOrder).count() == 1
    assert client.get(f"{PREFIX}/visits/{vid}/video/portrait/full").content == b"full-video"
    assert client.post(f"{PREFIX}/visits/{vid}/claim", headers=HEADERS).json()["order"]["claimed"]


def test_test_payment_success_mode_binding_and_callback_rejection(client, visitor_testing, monkeypatch):
    from app.core.config import get_settings
    pid, _ = visitor_testing
    vid = ready(client, pid)
    first = client.post(f"{PREFIX}/visits/{vid}/pay", headers=HEADERS).json()
    second = client.post(f"{PREFIX}/visits/{vid}/pay", headers=HEADERS).json()
    assert first == second
    body = {"out_trade_no": first["visit"]["order"]["order_no"], "trade_state": "SUCCESS", "amount": {"total": 1990, "currency": "CNY"}}
    assert client.post("/api/v1/scenic/payments/wechat/notify", headers={"Wechatpay-Signature": "VALID"}, json=body).status_code == 400
    payload = {"token": first["payment"]["token"], "outcome": "success"}
    monkeypatch.setattr(get_settings(), "visitor_payment_provider", "wechat")
    assert client.post(f"{PREFIX}/visits/{vid}/test-payment", headers=HEADERS, json=payload).status_code == 404
    assert client.post(f"{PREFIX}/visits/{vid}/pay", headers=HEADERS).status_code == 409
    monkeypatch.setattr(get_settings(), "visitor_payment_provider", "test")
    assert client.post(f"{PREFIX}/visits/{vid}/test-payment", headers=HEADERS, json=payload).json()["order"]["status"] == "paid"


@pytest.mark.parametrize("environment", ["production", "prod", "staging", "stage", "unknown"])
def test_test_endpoints_refuse_nonlocal_environments(client, visitor_testing, monkeypatch, environment):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "app_env", environment)
    assert client.get(PREFIX + "/runtime").status_code == 404
    assert client.post(PREFIX + "/auth/test", headers=HEADERS, json={"project_id": visitor_testing[0], "visitor": "A", "invite_code": "integration-invite-secret"}).status_code == 404


@pytest.mark.parametrize("environment", ["production", "prod", "staging", "stage", "unknown"])
def test_test_configuration_rejected_at_startup(monkeypatch, environment):
    from app.core import config
    monkeypatch.setattr(config, "Settings", lambda: SimpleNamespace(
        app_env=environment, visitor_auth_provider="test", visitor_payment_provider="wechat"))
    with pytest.raises(RuntimeError, match="仅允许"):
        config.get_settings.__wrapped__()


def test_test_configuration_requires_invite(monkeypatch):
    from app.core import config
    monkeypatch.setattr(config, "Settings", lambda: SimpleNamespace(
        app_env="development", visitor_auth_provider="wechat", visitor_payment_provider="test", visitor_test_invite_code=""))
    with pytest.raises(RuntimeError, match="16"):
        config.get_settings.__wrapped__()


def test_manual_request_admin_confirmation_and_delivery(client, setup_visitor, monkeypatch):
    import scenic_database as d
    from app.core.config import get_settings
    from app.services.visitor_auth import COOKIE
    from app.services.payments import WechatNativePaymentClient
    pid, user = setup_visitor
    monkeypatch.setattr(get_settings(), "visitor_payment_provider", "manual")
    monkeypatch.setattr(get_settings(), "wechat_pay_appid", "")
    def no_wechat(*args, **kwargs):
        raise AssertionError("manual mode must not contact WeChat Pay")
    monkeypatch.setattr(WechatNativePaymentClient, "__init__", no_wechat)
    vid = ready(client, pid)
    result = client.post(f"{PREFIX}/visits/{vid}/pay", headers=HEADERS, json={"price_cents": 1})
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["payment"] == {"provider": "manual"}
    order = data["visit"]["order"]
    assert order["amount_cents"] == 1990 and order["status"] == "pending"
    assert not data["visit"]["download_urls"]
    assert client.post(f"{PREFIX}/visits/{vid}/pay", headers=HEADERS).json()["visit"]["order"]["id"] == order["id"]
    assert client.post(f"{PREFIX}/visits/{vid}/payment-status", headers=HEADERS).json()["order"]["status"] == "pending"
    assert client.get(f"{PREFIX}/visits/{vid}/video/landscape/full").status_code == 402
    target = f"/api/v1/scenic/orders/{order['id']}/mark-paid"
    assert client.post(target).status_code == 401
    with d.SessionLocal() as db:
        db.query(d.User).filter_by(username=user["username"]).one().role = "viewer"
        db.commit()
    assert client.post(target, headers=user["headers"]).status_code == 403
    with d.SessionLocal() as db:
        db.query(d.User).filter_by(username=user["username"]).one().role = "operator"
        db.commit()
    queue = client.get(f"/api/v1/scenic/projects/{pid}/visitor-queue", headers=user["headers"])
    assert queue.json()["can_confirm_payment"] is True
    first = client.post(target, headers=user["headers"])
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "paid"
    assert client.post(target, headers=user["headers"]).status_code == 200
    with d.SessionLocal() as db:
        assert db.query(d.ScenicOrder).count() == 1
        assert db.query(d.AuditLog).filter_by(action="scenic.order_paid.manual_admin").count() == 1
    client.cookies.set(COOKIE, "two")
    assert client.post(f"{PREFIX}/visits/{vid}/pay", headers=HEADERS).status_code == 404
    assert client.get(f"{PREFIX}/visits/{vid}").status_code == 404
    client.cookies.set(COOKIE, "one")
    paid = client.post(f"{PREFIX}/visits/{vid}/payment-status", headers=HEADERS).json()
    assert paid["order"]["status"] == "paid"
    assert client.get(paid["download_urls"]["portrait"]).content == b"full-video"
    assert client.post(f"{PREFIX}/visits/{vid}/claim", headers=HEADERS).json()["order"]["claimed"] is True


def test_manual_confirmation_rejects_other_project_operator(client, setup_visitor, register_scenic_user, monkeypatch):
    import scenic_database as d
    from app.core.config import get_settings
    pid, _ = setup_visitor
    monkeypatch.setattr(get_settings(), "visitor_payment_provider", "manual")
    vid = ready(client, pid)
    order = client.post(f"{PREFIX}/visits/{vid}/pay", headers=HEADERS).json()["visit"]["order"]
    other = register_scenic_user()
    response = client.post(f"/api/v1/scenic/orders/{order['id']}/mark-paid", headers=other["headers"])
    assert response.status_code in {403, 404}
    # A staff operator in the same project can see requests, but cannot approve
    # payments unless granted project ownership or global admin permission.
    with d.SessionLocal() as db:
        db.query(d.User).filter_by(username=other["username"]).one().role = "operator"
        db.add(d.ProjectMember(project_id=pid, username=other["username"], role="operator"))
        db.commit()
    queue = client.get(f"/api/v1/scenic/projects/{pid}/visitor-queue", headers=other["headers"])
    assert queue.status_code == 200 and queue.json()["can_confirm_payment"] is False
    assert client.post(f"/api/v1/scenic/orders/{order['id']}/mark-paid", headers=other["headers"]).status_code == 403


def test_paid_delivery_page_handoff_without_wechat_cookie_and_receipt(client, setup_visitor, monkeypatch):
    import scenic_database as d
    from app.core.config import get_settings
    pid, staff = setup_visitor
    monkeypatch.setattr(get_settings(), "visitor_payment_provider", "manual")
    vid = ready(client, pid)
    pending = client.post(f"{PREFIX}/visits/{vid}/pay", headers=HEADERS).json()["visit"]
    assert not pending["delivery_page_url"]
    with d.SessionLocal() as db:
        order = db.get(d.ScenicOrder, pending["order"]["id"])
        token = order.delivery_token
        variants = {v.orientation: v.id for v in order.vlog_job.variants}
    page = f"{PREFIX}/deliveries/{token}/page"
    assert client.get(page).status_code == 402
    assert client.post(f"/api/v1/scenic/orders/{pending['order']['id']}/mark-paid", headers=staff["headers"]).status_code == 200
    paid = client.get(f"{PREFIX}/visits/{vid}").json()
    assert paid["delivery_page_url"] == page
    client.cookies.clear()  # Simulate a fresh system browser after WeChat handoff.
    assert client.get(f"{PREFIX}/visits/{vid}").status_code == 401
    response = client.get(page)
    assert response.status_code == 200
    assert "无需再次微信登录" in response.text
    assert "Cache-Control" in response.headers and "no-store" in response.headers["cache-control"]
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "/api/v1/scenic/deliveries/" not in response.text
    for orientation, variant_id in variants.items():
        asset = f"{PREFIX}/deliveries/{token}/variants/{variant_id}/full"
        assert client.get(asset + "?download=true").content == b"full-video"
        assert client.get(asset, headers={"Range": "bytes=0-3"}).content == b"full"
    receipt = f"{PREFIX}/deliveries/{token}/confirm-receipt"
    first = client.post(receipt, follow_redirects=False)
    assert first.status_code == 303 and first.headers["location"] == page
    assert client.post(receipt).status_code == 200
    with d.SessionLocal() as db:
        assert db.get(d.ScenicOrder, paid["order"]["id"]).claimed_at
        assert db.query(d.AuditLog).filter_by(action="scenic.order_receipt_confirmed").count() == 1
        db.get(d.ScenicOrder, paid["order"]["id"]).status = "pending"
        db.commit()
    assert client.get(page).status_code == 402
    assert client.get(asset).status_code == 402
    assert client.post(receipt).status_code == 402


def test_delivery_capability_rejects_tampering_other_versions_and_expiry(client, setup_visitor, monkeypatch):
    import scenic_database as d
    from app.core.config import get_settings
    from app.services.time_utils import utc_now
    pid, staff = setup_visitor
    monkeypatch.setattr(get_settings(), "visitor_payment_provider", "manual")
    vid = ready(client, pid)
    order = client.post(f"{PREFIX}/visits/{vid}/pay", headers=HEADERS).json()["visit"]["order"]
    assert client.post(f"/api/v1/scenic/orders/{order['id']}/mark-paid", headers=staff["headers"]).status_code == 200
    with d.SessionLocal() as db:
        paid = db.get(d.ScenicOrder, order["id"])
        token = paid.delivery_token
        extra = d.ScenicVlogJob(session_id=paid.session_id, created_by_username=staff["username"], status="succeeded")
        db.add(extra); db.flush()
        variant = d.ScenicVlogVariant(vlog_job_id=extra.id, orientation="landscape", status="succeeded", full_object_key="unrelated.mp4")
        db.add(variant); db.commit()
        other_id = variant.id
    client.cookies.clear()
    assert client.get(f"{PREFIX}/deliveries/{token}tampered/page").status_code == 404
    assert client.get(f"{PREFIX}/deliveries/{token}/variants/{other_id}/full").status_code == 404
    with d.SessionLocal() as db:
        db.get(d.ScenicOrder, order["id"]).paid_at = utc_now() - timedelta(days=366)
        db.commit()
    assert client.get(f"{PREFIX}/deliveries/{token}/page").status_code == 410
    assert client.get(f"{PREFIX}/deliveries/{token}/variants/{other_id}/full").status_code == 410
    assert client.post(f"{PREFIX}/deliveries/{token}/confirm-receipt").status_code == 410


@pytest.mark.parametrize("url,enabled,secure,allowed", [
    ("http://192.168.1.100:5174", True, False, True),
    ("http://192.168.1.100:5174", False, False, False),
    ("http://192.168.1.100:5174", True, True, False),
    ("http://8.8.8.8", True, False, False),
    ("http://127.0.0.1", True, False, False),
    ("http://192.168.1.100:5174/visitor/#/sites/1", True, False, False),
    ("https://visitor.example.com", False, True, True),
])
def test_lan_oauth_origin_boundary(url, enabled, secure, allowed):
    from app.services.visitor_auth import visitor_origin_allowed
    settings = SimpleNamespace(visitor_public_base_url=url, visitor_allow_lan_http=enabled, visitor_cookie_secure=secure)
    assert visitor_origin_allowed(settings) is allowed


def test_lan_oauth_redirect_and_signed_callback(client, setup_visitor, monkeypatch):
    from urllib.parse import urlsplit, parse_qs
    from app.core.config import get_settings
    from app.api.scenic import visitor_routes
    pid, _ = setup_visitor
    settings = get_settings()
    monkeypatch.setattr(settings, "visitor_public_base_url", "http://192.168.1.100:5174")
    monkeypatch.setattr(settings, "visitor_allow_lan_http", True)
    monkeypatch.setattr(settings, "visitor_cookie_secure", False)
    monkeypatch.setattr(settings, "visitor_wechat_appid", "test-公众号")
    monkeypatch.setattr(settings, "visitor_wechat_secret", "not-a-real-secret")
    response = client.get(f"{PREFIX}/auth/wechat?project_id={pid}", follow_redirects=False)
    assert response.status_code == 307
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["redirect_uri"] == ["http://192.168.1.100:5174" + PREFIX + "/auth/callback"]
    assert "Secure" not in response.headers["set-cookie"]
    class Wechat:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url, params):
            assert params["code"] == "valid-code"
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"openid": "real-oauth-identity"})
    monkeypatch.setattr(visitor_routes.httpx, "Client", Wechat)
    result = client.get(PREFIX + "/auth/callback", params={"code": "valid-code", "state": query["state"][0]}, follow_redirects=False)
    assert result.status_code == 303, result.text
    assert result.headers["location"] == f"http://192.168.1.100:5174/visitor/#/sites/{pid}/register"
    assert client.get(PREFIX + "/me").json()["authenticated"] is True


@pytest.fixture
def selectable_visit(client, setup_visitor):
    import cv2
    import numpy as np
    import scenic_database as d
    from app.core.config import get_settings
    from app.services.storage import storage
    from app.services.time_utils import utc_now
    pid, user = setup_visitor
    vid = register(client, pid).json()["id"]
    source = get_settings().object_storage_dir / "self-source.avi"
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"MJPG"), 10, (240, 160))
    assert writer.isOpened()
    for i in range(200):
        frame = np.full((160, 240, 3), (30 + i % 100, 110, 80), dtype=np.uint8)
        cv2.putText(frame, str(i), (20, 100), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        writer.write(frame)
    writer.release()
    stored = storage.save_path(f"scenic/{pid}", source, "self-source.avi", category="tests")
    thumbnail = storage.save_upload(f"scenic/{pid}", "thumb.jpg", io.BytesIO(b"private-thumb"), category="tests")
    now = utc_now()
    with d.SessionLocal() as db:
        row = db.get(d.VisitorVisit, vid)
        row.session.created_at = now - timedelta(minutes=1)
        row.session.checked_out_at = now
        row.stage = "selecting"
        media = d.MediaFile(project_id=pid, uploader_username=user["username"], filename="self-source.avi",
            object_key=stored.object_key, size_bytes=stored.size_bytes, content_type="video/x-msvideo", media_type="camera", status="indexed", created_at=now - timedelta(seconds=30))
        db.add(media); db.flush()
        for index, start in enumerate((0, 6, 12)):
            db.add(d.TouristVisitSegment(session_id=row.session_id, project_id=pid, media_id=media.id, camera_name=f"点位{index+1}",
                start_seconds=start, end_seconds=start+4, similarity=.91, original_media_object_key=media.object_key,
                thumbnail_object_key=thumbnail.object_key, included=True, sort_order=index))
        db.commit()
    return vid, pid, user


@pytest.mark.parametrize("identity", ["browser", "wechat"])
def test_visitor_select_edit_render_staff_payment_download_flow(client, selectable_visit, monkeypatch, identity):
    import copy
    import scenic_database as d
    from app.core.config import get_settings
    from app.services.job_manager import job_manager
    from app.services.storage import storage
    from app.services.visitor_production import production_step
    vid, pid, user = selectable_visit
    from app.api.scenic import tourist_routes
    from app.services import camera_stream
    from app.services.visitor_production import prepare_visit_recordings
    # Existing fixture segments stand in for model output; exercise the actual
    # finish/seal/match state machine before accessing the visitor editor.
    with d.SessionLocal() as db:
        row = db.get(d.VisitorVisit, vid)
        if identity == "browser":
            db.get(d.VisitorAccount, row.account_id).appid = "visitor-browser"
        row.stage = "visiting"
        row.session.checked_out_at = None
        assert row.verified_at is None
        db.commit()
    assert client.get(PREFIX + "/me").json()["auth_provider"] == identity
    finished = client.post(f"{PREFIX}/visits/{vid}/finish", headers=HEADERS)
    assert finished.json()["stage"] == "sealing"
    matched = []
    monkeypatch.setattr(camera_stream, "solidify_project_camera_recordings", lambda *args: None)
    monkeypatch.setattr(tourist_routes, "v1_match_tourist_segments", lambda *args: matched.append(args[0]))
    prepare_visit_recordings(vid)
    production_step(vid)
    assert len(matched) == 1
    assert client.get(f"{PREFIX}/visits/{vid}").json()["stage"] == "selecting"
    monkeypatch.setattr(get_settings(), "visitor_payment_provider", "manual")
    queued = []
    monkeypatch.setattr(job_manager, "enqueue", lambda *args: queued.append(args))
    url = f"{PREFIX}/visits/{vid}"
    editor = client.get(url + "/editor").json()
    assert len(editor["segments"]) == 3
    ids = [segment["id"] for segment in editor["segments"]]
    preview_response = client.get(editor["segments"][0]["preview_url"])
    assert preview_response.status_code == 200, preview_response.text if preview_response.status_code != 200 else ""
    assert preview_response.headers["content-type"] == "video/mp4"
    import cv2
    preview_path = get_settings().object_storage_dir / "matched-preview-check.mp4"
    preview_path.write_bytes(preview_response.content)
    cap = cv2.VideoCapture(str(preview_path))
    assert cap.isOpened()
    assert 3.8 <= cap.get(cv2.CAP_PROP_FRAME_COUNT) / cap.get(cv2.CAP_PROP_FPS) <= 4.1
    cap.release()
    body = {"segment_ids": [ids[0], ids[2]], "match_revision": editor["match_revision"], "config": {"music_id": "none", "title": "我的自助短片"}}
    plan = client.post(url + "/draft", headers=HEADERS, json=body)
    assert plan.status_code == 200, plan.text
    assert plan.json()["visit"]["stage"] == "editing"
    assert client.post(url + "/draft", headers=HEADERS, json=body).json()["draft"]["id"] == plan.json()["draft"]["id"]
    public = plan.json()["draft"]
    assert client.get(public["data"]["sources"][0]["preview_url"]).status_code == 200
    source = public["data"]["sources"][0]
    clip_start = source["available_start_seconds"]
    short_preview = client.get(source["preview_url"], params={"start_seconds": clip_start, "end_seconds": clip_start + .5})
    assert short_preview.status_code == 200
    preview_path.write_bytes(short_preview.content)
    cap = cv2.VideoCapture(str(preview_path))
    assert cap.isOpened()
    assert .4 <= cap.get(cv2.CAP_PROP_FRAME_COUNT) / cap.get(cv2.CAP_PROP_FPS) <= .6
    cap.release()
    assert client.get(source["preview_url"], params={"start_seconds": clip_start, "end_seconds": source["available_end_seconds"] + .5}).status_code == 422
    assert not any(word in json.dumps(public) for word in ('"object_key"', '"media_id"', '"source_sha256"', '"session_id"', 'original_media_url'))
    edited = copy.deepcopy(public["data"])
    edited["title"] = "游客确认的标题"
    edited["variants"]["portrait"][0]["crop_mode"] = "focus"
    edited["variants"]["portrait"][0]["focus_x"] = .3
    edited["variants"]["landscape"][0]["object_key"] = "forged-camera-key"
    save = client.put(url + "/draft", headers=HEADERS, json={"revision": public["revision"], "data": edited})
    assert save.status_code == 200, save.text
    revision = save.json()["draft"]["revision"]
    assert client.put(url + "/draft", headers=HEADERS, json={"revision": public["revision"], "data": edited}).status_code == 409
    assert client.post(url + "/render", headers=HEADERS, json={"revision": revision+1}).status_code == 409
    submitted = client.post(url + "/render", headers=HEADERS, json={"revision": revision})
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["stage"] == "rendering"
    duplicate = client.post(url + "/render", headers=HEADERS, json={"revision": revision})
    assert duplicate.status_code == 200, duplicate.text
    with d.SessionLocal() as db:
        row = db.get(d.VisitorVisit, vid)
        assert db.query(d.ScenicVlogJob).count() == 1
        snapshot = json.loads(row.vlog.snapshot_json)
        assert snapshot["title"] == "游客确认的标题"
        assert snapshot["variants"]["portrait"][0]["focus_x"] == .3
        assert snapshot["variants"]["landscape"][0]["object_key"] != "forged-camera-key"
        for variant in row.vlog.variants:
            full = storage.save_upload(f"scenic/{pid}", variant.orientation + ".mp4", io.BytesIO(b"full-video"), category="tests")
            preview = storage.save_upload(f"scenic/{pid}", variant.orientation + "preview.mp4", io.BytesIO(b"preview-video"), category="tests")
            variant.status, variant.duration_seconds, variant.full_object_key, variant.preview_object_key = "succeeded", 4, full.object_key, preview.object_key
        row.vlog.status = "succeeded"
        db.commit()
    production_step(vid)
    assert client.get(url).json()["stage"] == "ready"
    assert client.get(url + "/video/portrait/preview").content == b"preview-video"
    payment = client.post(url + "/pay", headers=HEADERS)
    assert payment.status_code == 200, payment.text
    assert payment.json()["payment"]["provider"] == "manual"
    assert client.get(url + "/video/portrait/full").status_code == 402
    assert client.post(url + "/draft", headers=HEADERS, json=body).status_code == 409
    oid = payment.json()["visit"]["order"]["id"]
    assert client.post(f"/api/v1/scenic/orders/{oid}/mark-paid", headers=user["headers"]).status_code == 200
    assert client.get(url + "/video/landscape/full").content == b"full-video"
    assert client.post(url + "/claim", headers=HEADERS).json()["order"]["claimed"]
    assert len(queued) == 2


def test_visitor_editor_ownership_scope_and_matched_range(client, selectable_visit):
    import scenic_database as d
    from app.services.visitor_auth import COOKIE
    vid, pid, _ = selectable_visit
    url = f"{PREFIX}/visits/{vid}"
    editor = client.get(url + "/editor").json()
    sid = editor["segments"][0]["id"]
    client.cookies.set(COOKIE, "two")
    assert client.get(url + "/editor").status_code == 404
    assert client.get(f"{url}/segments/{sid}/thumbnail").status_code == 404
    assert client.post(url + "/draft", headers=HEADERS, json={"segment_ids": [sid], "match_revision": editor["match_revision"]}).status_code == 404
    client.cookies.set(COOKIE, "one")
    body = {"segment_ids": [sid+1000], "match_revision": editor["match_revision"], "config": {"music_id": "none"}}
    assert client.post(url + "/draft", headers=HEADERS, json=body).status_code == 422
    body["segment_ids"] = [sid]
    draft = client.post(url + "/draft", headers=HEADERS, json=body).json()["draft"]
    clip = draft["data"]["variants"]["landscape"][0]
    clip["end_seconds"] = clip["available_end_seconds"] + 1
    assert client.put(url + "/draft", headers=HEADERS, json={"revision": draft["revision"], "data": draft["data"]}).status_code == 422
    with d.SessionLocal() as db:
        row = db.get(d.VisitorVisit, vid)
        row.session.created_at = row.session.checked_out_at + timedelta(seconds=1)
        db.commit()
    assert not client.get(url + "/editor").json()["segments"]
    assert client.get(url + "/editor").json()["draft"] is None
    assert client.get(f"{url}/segments/{sid}/preview").status_code == 404
    assert client.get(draft["data"]["sources"][0]["preview_url"]).status_code == 404
    assert client.post(url + "/render", headers=HEADERS, json={"revision": draft["revision"]}).status_code == 409


def test_visitor_can_rematch_without_staff(client, selectable_visit):
    vid, _, _ = selectable_visit
    url = f"{PREFIX}/visits/{vid}"
    previous = client.get(url).json()["checked_out_at"]
    response = client.post(url + "/retry", headers=HEADERS, json={"action": "match"})
    assert response.status_code == 200, response.text
    assert response.json()["stage"] == "waiting"
    assert response.json()["checked_out_at"] == previous
    assert client.post(url + "/retry", headers=HEADERS).status_code == 409



def test_live_camera_visit_scope_uses_capture_anchor_despite_encoded_time_drift():
    from app.services.visitor_production import within_visit
    from app.services.time_utils import utc_now
    now = utc_now()
    session = SimpleNamespace(project_id=1, created_at=now-timedelta(seconds=30), checked_out_at=now)
    media = SimpleNamespace(project_id=1, media_type="camera", created_at=now-timedelta(hours=2), scenic_metadata=None)
    target = SimpleNamespace(media_file=media, timestamp=(now-timedelta(seconds=10)).isoformat(), source_info="video_seek_seconds=50.000")
    assert within_visit(target, session, 48, 52)
    assert not within_visit(target, session, 10, 14)
    target.timestamp = (now-timedelta(days=1)).isoformat()
    assert not within_visit(target, session, 48, 52)


def test_legacy_camera_reference_recovers_only_original_timestamp(client, selectable_visit, monkeypatch):
    import scenic_database as d
    from unittest.mock import Mock
    from app.api.scenic import editing_routes
    from app.core.config import get_settings
    from app.services.storage import S3ObjectStorage
    vid, pid, _ = selectable_visit
    fake = S3ObjectStorage.__new__(S3ObjectStorage)
    fake.path_for = Mock(side_effect=FileNotFoundError('missing'))
    fake.publish_path = Mock()
    monkeypatch.setattr(editing_routes,'storage',fake)
    with d.SessionLocal() as db:
        row=db.get(d.VisitorVisit,vid); media=row.session.segments[0].media_file
        root=get_settings().object_storage_dir
        prefix=f'projects/scenic/{pid}/media/{media.id}/recordings/'
        expected=prefix+f'central_{media.created_at.strftime("%Y%m%d_%H%M%S_%f")}_4.avi'
        next_key=prefix+'central_20990101_000000_000000_5.avi'
        alias=root/expected; alias.parent.mkdir(parents=True); alias.write_bytes(b'original-sealed-recording')
        (root/next_key).write_bytes(b'wrong-next-recording')
        media.object_key=next_key
        for segment in row.session.segments: segment.original_media_object_key=next_key
        db.commit()
        editing_routes.prepare_camera_sources(row.session,db,{media.id})
        assert media.object_key==expected
        assert all(segment.original_media_object_key==expected for segment in row.session.segments)
        fake.publish_path.assert_called_once_with(expected,alias.resolve(),'video/x-msvideo')
        assert db.query(d.AuditLog).filter_by(action='scenic.camera_source_recovered').count()==1


def test_browser_entry_preserves_wechat_identity(client, setup_visitor, monkeypatch):
    from app.core.config import get_settings
    from app.services.visitor_auth import COOKIE
    settings = get_settings()
    monkeypatch.setattr(settings, "visitor_auth_provider", "browser")
    monkeypatch.setattr(settings, "visitor_wechat_appid", "visitor-app")
    monkeypatch.setattr(settings, "visitor_wechat_secret", "fixture-secret")
    pid, _ = setup_visitor
    runtime = client.get(PREFIX + "/runtime").json()
    assert runtime["browser_login_available"] is True
    assert runtime["wechat_login_available"] is True
    assert client.get(PREFIX + "/me").json()["auth_provider"] == "wechat"
    token = client.cookies.get(COOKIE)
    result = client.post(PREFIX + "/auth/browser", headers=HEADERS, json={"project_id": pid})
    assert result.json()["auth_provider"] == "wechat"
    assert client.cookies.get(COOKIE) == token
    client.cookies.clear()
    result = client.post(PREFIX + "/auth/browser", headers=HEADERS, json={"project_id": pid})
    assert result.json()["auth_provider"] == "browser"
    assert client.get(PREFIX + "/me").json()["auth_provider"] == "browser"


def test_unverified_visit_can_resume_selfservice_after_verification_becomes_optional(client, setup_visitor, monkeypatch):
    from app.core.config import get_settings
    settings = get_settings()
    pid, _ = setup_visitor
    monkeypatch.setattr(settings, "visitor_require_staff_verification", True)
    visit = register(client, pid).json()
    url = f"{PREFIX}/visits/{visit['id']}"
    assert client.post(url + "/finish", headers=HEADERS).json()["stage"] == "review"
    assert client.post(url + "/retry", headers=HEADERS).status_code == 403
    monkeypatch.setattr(settings, "visitor_require_staff_verification", False)
    result = client.post(url + "/retry", headers=HEADERS)
    assert result.status_code == 200
    assert result.json()["stage"] == "sealing"
    assert result.json()["identity_verified"] is False


def test_legacy_rounded_draft_can_save_and_render_without_widening_matches(client, selectable_visit, monkeypatch):
    import copy
    import scenic_database as d
    from app.services.job_manager import job_manager
    vid, _, _ = selectable_visit
    url = f"{PREFIX}/visits/{vid}"
    with d.SessionLocal() as db:
        row = db.get(d.VisitorVisit, vid)
        segment = sorted(row.session.segments, key=lambda item: item.start_seconds)[0]
        segment.start_seconds, segment.end_seconds = 0.000024, 3.167476
        db.commit()
    editor = client.get(url + "/editor").json()
    created = client.post(url + "/draft", headers=HEADERS, json={
        "segment_ids": [editor["segments"][0]["id"]], "match_revision": editor["match_revision"],
        "config": {"music_id": "none"}})
    assert created.status_code == 200, created.text
    # Reproduce already-persisted plans from before the precision fix.
    with d.SessionLocal() as db:
        draft = db.get(d.VisitorVisit, vid).draft
        data = json.loads(draft.data_json)
        for clips in data["variants"].values():
            clips[0]["start_seconds"] = 0.0
            clips[0]["end_seconds"] = 3.1675
        draft.data_json = json.dumps(data)
        db.commit()
    public = client.get(url + "/editor").json()["draft"]
    forged = copy.deepcopy(public["data"])
    forged["variants"]["landscape"][0]["end_seconds"] = 3.2
    forged["variants"]["landscape"][0]["available_end_seconds"] = 100
    assert client.put(url + "/draft", headers=HEADERS, json={"revision": public["revision"], "data": forged}).status_code == 422
    saved = client.put(url + "/draft", headers=HEADERS, json={"revision": public["revision"], "data": public["data"]})
    assert saved.status_code == 200, saved.text
    for clips in saved.json()["draft"]["data"]["variants"].values():
        assert clips[0]["start_seconds"] == 0.000024
        assert clips[0]["end_seconds"] == 3.167476
    queued = []
    monkeypatch.setattr(job_manager, "enqueue", lambda *args: queued.append(args))
    rendered = client.post(url + "/render", headers=HEADERS, json={"revision": saved.json()["draft"]["revision"]})
    assert rendered.status_code == 200, rendered.text
    assert rendered.json()["stage"] == "rendering"
    assert len(queued) == 2
