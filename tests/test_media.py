"""Tests for the M14 media services harness (app/control_plane/media.py).

No real network/GPU: the backend HTTP opener is injected with a fake, and the S3
client with a recorder. Covers gating, deny-by-default allow-list, cross-tenant
denial, per-tenant disable, rate limiting, size/content caps, per-tenant S3 key,
outcome→HTTP mapping, and audit content-safety (no prompt/transcript leak).
"""
import io
import json
import unittest
from http import HTTPStatus
from unittest import mock

from app.control_plane import media as M
from app.control_plane.agent_tools import RateLimiter
from app.control_plane.integrations import InMemoryTenantIntegrationState
from app.control_plane.media import (
    MediaExecutor,
    MediaService,
    build_media_generate_response,
    build_media_list_response,
    build_media_synthesize_response,
    build_media_transcribe_response,
    parse_media_allowlist,
    parse_media_services,
)
from app.control_plane.token_verifier import TokenClaims, TokenVerificationError


class _Verifier:
    def __init__(self, email):
        self._claims = TokenClaims(subject="user-x", email=email)

    def verify(self, raw):
        if raw != "valid":
            raise TokenVerificationError("bad")
        return self._claims


_ALICE = _Verifier("alice@tenant-a.test")
_BOB = _Verifier("bob@tenant-b.test")
_ALLOW = parse_media_allowlist(json.dumps({"tenant-a.test": ["whisper", "sdxl"]}))
_SERVICES = {
    "whisper": MediaService("whisper", "stt", "http://whisper-stt.inference.svc:8000"),
    "sdxl": MediaService("sdxl", "image", "http://sdxl.inference.svc:8000"),
}


class _Resp:
    def __init__(self, status, body, ctype="application/json"):
        self.status = status
        self._body = body
        self.headers = {"Content-Type": ctype}

    def read(self, n=-1):
        return self._body

    # urllib response uses .headers.get
    @property
    def headers_obj(self):
        return self.headers


class _Headers(dict):
    def get(self, k, default=None):
        return dict.get(self, k, default)


def _resp(status, body, ctype="application/json"):
    r = _Resp(status, body, ctype)
    r.headers = _Headers(r.headers)
    return r


class _Storage:
    def __init__(self):
        self.objects = {}

    def put_object(self, *, key, body, content_type="application/octet-stream"):
        self.objects[key] = (body, content_type)


def _executor(opener, storage=None):
    return MediaExecutor(services=dict(_SERVICES), opener=opener, storage_client=storage)


class TestParsing(unittest.TestCase):
    def test_allowlist_deny_by_default(self):
        self.assertEqual(parse_media_allowlist(None), {})
        self.assertEqual(parse_media_allowlist("nope"), {})

    def test_services_parse_and_reject_bad(self):
        s = parse_media_services(json.dumps({
            "whisper": {"kind": "stt", "base_url": "http://x:8000/"},
            "bad": {"kind": "video", "base_url": "http://y"},     # bad kind
            "nourl": {"kind": "image"},                            # missing url
        }))
        self.assertEqual(set(s), {"whisper"})
        self.assertEqual(s["whisper"].base_url, "http://x:8000")  # trailing / stripped

    def test_tts_kind_parsed(self):
        s = parse_media_services(json.dumps({
            "piper": {"kind": "tts", "base_url": "http://p:8000", "model": "tts-1"},
        }))
        self.assertEqual(set(s), {"piper"})
        self.assertEqual(s["piper"].kind, "tts")
        self.assertEqual(s["piper"].model, "tts-1")


class TestGatingAndList(unittest.TestCase):
    def test_anonymous_unauthorized(self):
        status, _ = build_media_list_response(
            authorization=None, body="", token_verifier=_ALICE, enabled=True,
            allowlist=_ALLOW, executor=_executor(lambda *a, **k: None))
        self.assertEqual(status, HTTPStatus.UNAUTHORIZED)

    def test_kill_switch_off(self):
        status, payload = build_media_list_response(
            authorization="Bearer valid", body="", token_verifier=_ALICE, enabled=False,
            allowlist=_ALLOW, executor=_executor(lambda *a, **k: None))
        self.assertEqual(status, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(payload["error"], "media_disabled")

    def test_list_allowlisted(self):
        status, payload = build_media_list_response(
            authorization="Bearer valid", body="", token_verifier=_ALICE, enabled=True,
            allowlist=_ALLOW, executor=_executor(lambda *a, **k: None))
        self.assertEqual(payload["services"], ["sdxl", "whisper"])

    def test_list_empty_for_other_tenant(self):
        status, payload = build_media_list_response(
            authorization="Bearer valid", body="", token_verifier=_BOB, enabled=True,
            allowlist=_ALLOW, executor=_executor(lambda *a, **k: None))
        self.assertEqual(payload["services"], [])


def _transcribe(service, audio, *, verifier=_ALICE, enabled=True, allowlist=_ALLOW,
                executor=None, rl=None, state=None, store=None, max_audio=25 * 1024 * 1024):
    return build_media_transcribe_response(
        authorization="Bearer valid", service=service, body=audio, content_type="audio/wav",
        token_verifier=verifier, enabled=enabled, allowlist=allowlist,
        executor=executor or _executor(lambda *a, **k: _resp(200, b'{"text":"hi"}')),
        rate_limiter=rl or RateLimiter(), tenant_state=state or InMemoryTenantIntegrationState(),
        max_audio_bytes=max_audio, notification_store=store)


class TestTranscribe(unittest.TestCase):
    def test_success(self):
        status, payload = _transcribe("whisper", b"RIFFfakeaudio")
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload["result"]["text"], "hi")

    def test_not_allowlisted(self):
        status, payload = _transcribe("whisper", b"x", verifier=_BOB)
        self.assertEqual(status, HTTPStatus.FORBIDDEN)
        self.assertEqual(payload["error"], "service_not_allowed")

    def test_tenant_disabled(self):
        st = InMemoryTenantIntegrationState()
        st.disable("tenant-a.test", "whisper")
        status, payload = _transcribe("whisper", b"x", state=st)
        self.assertEqual(payload["error"], "tenant_disabled")

    def test_audio_too_large(self):
        status, payload = _transcribe("whisper", b"x" * 100, max_audio=10)
        self.assertEqual(status, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        self.assertEqual(payload["error"], "audio_too_large")

    def test_missing_service(self):
        status, _ = _transcribe(None, b"x")
        self.assertEqual(status, HTTPStatus.BAD_REQUEST)

    def test_empty_audio(self):
        status, _ = _transcribe("whisper", b"")
        self.assertEqual(status, HTTPStatus.BAD_REQUEST)

    def test_rate_limited(self):
        rl = RateLimiter(per_minute=1, max_concurrency=4)
        first, _ = _transcribe("whisper", b"x", rl=rl)
        self.assertEqual(first, HTTPStatus.OK)
        second, payload = _transcribe("whisper", b"x", rl=rl)
        self.assertEqual(second, HTTPStatus.TOO_MANY_REQUESTS)

    def test_wrong_kind(self):
        # sdxl is an image service; transcribe must reject it.
        status, payload = _transcribe("sdxl", b"x")
        self.assertEqual(status, HTTPStatus.BAD_REQUEST)
        self.assertEqual(payload["error"], "wrong_service_kind")

    def test_backend_timeout(self):
        def boom(*a, **k):
            raise TimeoutError()
        status, payload = _transcribe("whisper", b"x", executor=_executor(boom))
        self.assertEqual(status, HTTPStatus.GATEWAY_TIMEOUT)

    def test_request_is_multipart_with_model_and_file(self):
        seen = {}

        def capture(req, timeout=None):
            seen["url"] = req.full_url
            seen["ctype"] = req.headers.get("Content-type")
            seen["body"] = req.data
            return _resp(200, b'{"text":"hi"}')

        status, _ = _transcribe("whisper", b"AUDIOBYTES", executor=_executor(capture))
        self.assertEqual(status, HTTPStatus.OK)
        self.assertTrue(seen["url"].endswith("/v1/audio/transcriptions"))
        self.assertTrue(seen["ctype"].startswith("multipart/form-data; boundary="))
        self.assertIn(b'name="file"', seen["body"])
        self.assertIn(b'name="model"', seen["body"])
        self.assertIn(b"AUDIOBYTES", seen["body"])


def _generate(body, *, verifier=_ALICE, enabled=True, allowlist=_ALLOW, executor=None,
              rl=None, state=None, store=None, max_prompt=2000):
    return build_media_generate_response(
        authorization="Bearer valid", body=json.dumps(body), token_verifier=verifier,
        enabled=enabled, allowlist=allowlist,
        executor=executor or _executor(lambda *a, **k: _resp(200, b"PNGBYTES", "image/png"), _Storage()),
        rate_limiter=rl or RateLimiter(), tenant_state=state or InMemoryTenantIntegrationState(),
        max_prompt_chars=max_prompt, notification_store=store)


_GEN = {"service": "sdxl", "prompt": "a cat", "params": {}}


class TestGenerate(unittest.TestCase):
    def test_success_stores_per_tenant_artifact(self):
        store = _Storage()
        ex = _executor(lambda *a, **k: _resp(200, b"PNGBYTES", "image/png"), store)
        status, payload = _generate(_GEN, executor=ex)
        self.assertEqual(status, HTTPStatus.OK)
        self.assertTrue(payload["result"]["stored"])
        # Artifact key is scoped to the tenant + user.
        key = payload["result"]["key"]
        self.assertTrue(key.startswith("media/tenant-a.test/user-x/"))
        self.assertIn(key, store.objects)

    def test_prompt_too_long(self):
        status, payload = _generate({"service": "sdxl", "prompt": "x" * 5000}, max_prompt=2000)
        self.assertEqual(status, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        self.assertEqual(payload["error"], "prompt_too_long")

    def test_cross_tenant_denied(self):
        status, payload = _generate(_GEN, verifier=_BOB)
        self.assertEqual(status, HTTPStatus.FORBIDDEN)

    def test_wrong_kind(self):
        status, payload = _generate({"service": "whisper", "prompt": "x"})
        self.assertEqual(payload["error"], "wrong_service_kind")

    def test_unknown_service(self):
        allow = parse_media_allowlist(json.dumps({"tenant-a.test": ["ghost"]}))
        status, payload = _generate({"service": "ghost", "prompt": "x"}, allowlist=allow)
        self.assertEqual(status, HTTPStatus.NOT_FOUND)
        self.assertEqual(payload["error"], "unknown_service")

    def test_missing_prompt(self):
        status, _ = _generate({"service": "sdxl"})
        self.assertEqual(status, HTTPStatus.BAD_REQUEST)


_TTS_SERVICES = {
    "piper": MediaService("piper", "tts", "http://piper-tts.inference.svc:8000", model="tts-1"),
    "whisper": MediaService("whisper", "stt", "http://whisper-stt.inference.svc:8000"),
}
_TTS_ALLOW = parse_media_allowlist(json.dumps({"tenant-a.test": ["piper", "whisper"]}))


def _tts_executor(opener, storage=None):
    return MediaExecutor(services=dict(_TTS_SERVICES), opener=opener, storage_client=storage)


def _synthesize(body, *, verifier=_ALICE, enabled=True, allowlist=_TTS_ALLOW, executor=None,
                rl=None, state=None, store=None, max_text=2000):
    return build_media_synthesize_response(
        authorization="Bearer valid", body=json.dumps(body), token_verifier=verifier,
        enabled=enabled, allowlist=allowlist,
        executor=executor or _tts_executor(lambda *a, **k: _resp(200, b"MP3BYTES", "audio/mpeg"), _Storage()),
        rate_limiter=rl or RateLimiter(), tenant_state=state or InMemoryTenantIntegrationState(),
        max_text_chars=max_text, notification_store=store)


class TestSynthesize(unittest.TestCase):
    def test_success_stores_audio_artifact(self):
        store = _Storage()
        ex = _tts_executor(lambda *a, **k: _resp(200, b"MP3BYTES", "audio/mpeg"), store)
        status, payload = _synthesize({"service": "piper", "text": "hello", "params": {}}, executor=ex)
        self.assertEqual(status, HTTPStatus.OK)
        self.assertTrue(payload["result"]["stored"])
        key = payload["result"]["key"]
        self.assertTrue(key.startswith("media/tenant-a.test/user-x/"))
        self.assertEqual(store.objects[key][1], "audio/mpeg")  # audio content-type preserved

    def test_text_too_long(self):
        status, payload = _synthesize({"service": "piper", "text": "x" * 5000}, max_text=2000)
        self.assertEqual(status, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        self.assertEqual(payload["error"], "text_too_long")

    def test_wrong_kind(self):
        status, payload = _synthesize({"service": "whisper", "text": "hi"})
        self.assertEqual(payload["error"], "wrong_service_kind")

    def test_cross_tenant_denied(self):
        status, payload = _synthesize({"service": "piper", "text": "hi"}, verifier=_BOB)
        self.assertEqual(status, HTTPStatus.FORBIDDEN)

    def test_missing_text(self):
        status, _ = _synthesize({"service": "piper"})
        self.assertEqual(status, HTTPStatus.BAD_REQUEST)

    def test_request_sends_input_and_model_to_speech_endpoint(self):
        captured = {}

        def opener(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = req.data
            return _resp(200, b"MP3", "audio/mpeg")

        ex = _tts_executor(opener, _Storage())
        _synthesize({"service": "piper", "text": "hello world", "params": {"voice": "alloy"}}, executor=ex)
        self.assertTrue(captured["url"].endswith("/v1/audio/speech"))
        sent = json.loads(captured["body"])
        self.assertEqual(sent["input"], "hello world")
        self.assertEqual(sent["model"], "tts-1")
        self.assertEqual(sent["voice"], "alloy")  # params merged through


class _PresignStorage:
    def __init__(self, exists=True):
        self.exists = exists
        self.last_key = None

    def generate_presigned_url(self, *, key, expires_in=300, operation="get_object"):
        self.last_key = key
        if not self.exists:
            raise Exception("not found")
        return f"https://s3.example/{key}?sig=abc"


class TestArtifactFetch(unittest.TestCase):
    def test_presigned_url_for_owned_artifact(self):
        from app.control_plane.media import build_media_artifact_response
        storage = _PresignStorage()
        ex = MediaExecutor(storage_client=storage)
        status, payload = build_media_artifact_response(
            authorization="Bearer valid", artifact_id="abc-123", token_verifier=_ALICE, executor=ex)
        self.assertEqual(status, HTTPStatus.OK)
        self.assertIn("s3.example", payload["url"])
        # Key is scoped to the caller's tenant + user.
        self.assertEqual(storage.last_key, "media/tenant-a.test/user-x/abc-123.bin")

    def test_missing_artifact_404(self):
        from app.control_plane.media import build_media_artifact_response
        ex = MediaExecutor(storage_client=_PresignStorage(exists=False))
        status, _ = build_media_artifact_response(
            authorization="Bearer valid", artifact_id="abc-123", token_verifier=_ALICE, executor=ex)
        self.assertEqual(status, HTTPStatus.NOT_FOUND)

    def test_cross_tenant_artifact_fetch_is_404(self):
        # Storage physically holds only ALICE's object. BOB requesting the SAME
        # artifact_id addresses a DIFFERENT key (his own tenant/user prefix), so
        # isolation-by-key-reconstruction denies him — even knowing the id.
        from app.control_plane.media import build_media_artifact_response

        class _OnlyAlice:
            def generate_presigned_url(self, *, key, expires_in=300, operation="get_object"):
                if key == "media/tenant-a.test/user-x/abc-123.bin":
                    return f"https://s3.example/{key}"
                raise Exception("not found")

        ex = MediaExecutor(storage_client=_OnlyAlice())
        owner_status, _ = build_media_artifact_response(
            authorization="Bearer valid", artifact_id="abc-123", token_verifier=_ALICE, executor=ex)
        self.assertEqual(owner_status, HTTPStatus.OK)                 # owner can fetch
        other_status, _ = build_media_artifact_response(
            authorization="Bearer valid", artifact_id="abc-123", token_verifier=_BOB, executor=ex)
        self.assertEqual(other_status, HTTPStatus.NOT_FOUND)          # other tenant cannot

    def test_bad_artifact_id_rejected(self):
        from app.control_plane.media import build_media_artifact_response
        ex = MediaExecutor(storage_client=_PresignStorage())
        status, _ = build_media_artifact_response(
            authorization="Bearer valid", artifact_id="../etc/passwd", token_verifier=_ALICE, executor=ex)
        self.assertEqual(status, HTTPStatus.BAD_REQUEST)

    def test_anonymous_unauthorized(self):
        from app.control_plane.media import build_media_artifact_response
        ex = MediaExecutor(storage_client=_PresignStorage())
        status, _ = build_media_artifact_response(
            authorization=None, artifact_id="abc-123", token_verifier=_ALICE, executor=ex)
        self.assertEqual(status, HTTPStatus.UNAUTHORIZED)


class _ContentStorage:
    def __init__(self, data=None):
        self._data = data  # (bytes, content_type) or None

    def get_object_with_type(self, *, key):
        self.last_key = key
        if self._data is None:
            raise Exception("not found")
        return self._data


class TestArtifactContent(unittest.TestCase):
    def test_streams_bytes_with_content_type(self):
        from app.control_plane.media import build_media_artifact_content
        storage = _ContentStorage((b"PNGDATA", "image/png"))
        ex = MediaExecutor(storage_client=storage)
        status, ctype, body = build_media_artifact_content(
            authorization="Bearer valid", artifact_id="abc-1", token_verifier=_ALICE, executor=ex)
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(ctype, "image/png")
        self.assertEqual(body, b"PNGDATA")
        self.assertEqual(storage.last_key, "media/tenant-a.test/user-x/abc-1.bin")

    def test_missing_404(self):
        from app.control_plane.media import build_media_artifact_content
        ex = MediaExecutor(storage_client=_ContentStorage(None))
        status, ctype, body = build_media_artifact_content(
            authorization="Bearer valid", artifact_id="abc-1", token_verifier=_ALICE, executor=ex)
        self.assertEqual(status, HTTPStatus.NOT_FOUND)

    def test_bad_id_400(self):
        from app.control_plane.media import build_media_artifact_content
        ex = MediaExecutor(storage_client=_ContentStorage((b"x", "image/png")))
        status, _, _ = build_media_artifact_content(
            authorization="Bearer valid", artifact_id="../x", token_verifier=_ALICE, executor=ex)
        self.assertEqual(status, HTTPStatus.BAD_REQUEST)

    def test_anonymous_401(self):
        from app.control_plane.media import build_media_artifact_content
        ex = MediaExecutor(storage_client=_ContentStorage((b"x", "image/png")))
        status, ctype, _ = build_media_artifact_content(
            authorization=None, artifact_id="abc-1", token_verifier=_ALICE, executor=ex)
        self.assertEqual(status, HTTPStatus.UNAUTHORIZED)
        self.assertEqual(ctype, "application/json")


class TestAuditContentSafety(unittest.TestCase):
    def test_prompt_not_in_audit(self):
        with self.assertLogs("app.control_plane.agent_tools", level="INFO") as cm:
            _generate({"service": "sdxl", "prompt": "SECRET-PROMPT-TEXT"})
        records = [r for r in cm.records if hasattr(r, "audit")]
        self.assertTrue(records)
        dumped = json.dumps(records[-1].audit)
        self.assertNotIn("SECRET-PROMPT-TEXT", dumped)
        # Only shape (key + type/size) is recorded.
        self.assertIn("prompt", records[-1].audit["arg_shape"])
        self.assertEqual(records[-1].audit["arg_shape"]["prompt"]["type"], "str")


if __name__ == "__main__":
    unittest.main()
