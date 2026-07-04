"""Model install requests (design Phase 1a) — store, handlers, isolation, gating."""
import json
import types
from http import HTTPStatus
from unittest import TestCase

from app.control_plane import model_requests as mr
from app.control_plane.token_verifier import TokenClaims


def _config(enabled=True, allow="*", admin="admin", cap=25):
    return types.SimpleNamespace(
        model_install_enabled=enabled,
        model_install_allowlist=allow,
        model_install_max_open_per_tenant=cap,
        auth=types.SimpleNamespace(admin_group=admin),
    )


class _FakeTV:
    def __init__(self, sub, email, groups=()):
        self._c = TokenClaims(subject=sub, email=email, groups=frozenset(groups))

    def verify(self, _token):
        return self._c


class _FakeNotif:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


def _create(store, tv, body, config, notif=None):
    return mr.build_model_request_create_response(
        authorization="Bearer x", body=body, token_verifier=tv,
        store=store, config=config, notification_store=notif,
    )


def _rl_create(store, tv, config, rate_limiter):
    return mr.build_model_request_create_response(
        authorization="Bearer x", body='{"hf_repo_id":"meta-llama/x"}',
        token_verifier=tv, store=store, config=config, rate_limiter=rate_limiter,
    )


def _list(store, tv, config):
    return mr.build_model_requests_list_response(
        authorization="Bearer x", token_verifier=tv, store=store, config=config,
    )


class AllowlistTests(TestCase):
    def test_empty_denies_all(self):
        self.assertEqual(mr.parse_repo_allowlist(None), frozenset())
        self.assertFalse(mr.is_repo_allowed(frozenset(), "meta-llama/x"))

    def test_wildcard_allows_any(self):
        al = mr.parse_repo_allowlist("*")
        self.assertTrue(mr.is_repo_allowed(al, "anyone/anything"))

    def test_org_and_exact_match(self):
        al = mr.parse_repo_allowlist("meta-llama, org/exact-repo")
        self.assertTrue(mr.is_repo_allowed(al, "meta-llama/Llama-3.1-8B"))   # org prefix
        self.assertTrue(mr.is_repo_allowed(al, "org/exact-repo"))            # exact
        self.assertFalse(mr.is_repo_allowed(al, "org/other-repo"))           # not listed


class CreateTests(TestCase):
    def test_create_records_and_notifies(self):
        store, notif = mr.InMemoryModelRequestStore(), _FakeNotif()
        st, pl = _create(store, _FakeTV("alice", "alice@t-a.test"),
                         '{"hf_repo_id":"meta-llama/Llama-3.1-8B-Instruct"}', _config(), notif)
        self.assertEqual(st, HTTPStatus.ACCEPTED)
        self.assertEqual(pl["status"], "requested")
        self.assertEqual(pl["hf_repo_id"], "meta-llama/Llama-3.1-8B-Instruct")
        self.assertEqual(len(notif.events), 1)
        self.assertEqual(notif.events[0].event_class, "model_install_requested")

    def test_kill_switch_off_forbidden(self):
        st, pl = _create(mr.InMemoryModelRequestStore(), _FakeTV("a", "a@t.test"),
                         '{"hf_repo_id":"meta-llama/x"}', _config(enabled=False))
        self.assertEqual(st, HTTPStatus.FORBIDDEN)
        self.assertEqual(pl["error"], "model_install_disabled")

    def test_allowlist_denies_unlisted_repo(self):
        st, pl = _create(mr.InMemoryModelRequestStore(), _FakeTV("a", "a@t.test"),
                         '{"hf_repo_id":"evil/backdoor"}', _config(allow="meta-llama"))
        self.assertEqual(st, HTTPStatus.FORBIDDEN)
        self.assertEqual(pl["error"], "repo_not_allowed")

    def test_empty_allowlist_denies_all(self):
        st, _ = _create(mr.InMemoryModelRequestStore(), _FakeTV("a", "a@t.test"),
                        '{"hf_repo_id":"meta-llama/x"}', _config(allow=""))
        self.assertEqual(st, HTTPStatus.FORBIDDEN)

    def test_invalid_repo_id_rejected(self):
        st, _ = _create(mr.InMemoryModelRequestStore(), _FakeTV("a", "a@t.test"),
                        '{"hf_repo_id":"not-a-repo"}', _config())
        self.assertEqual(st, HTTPStatus.BAD_REQUEST)

    def test_oversized_repo_id_rejected(self):
        # A regex-matching but absurdly long repo id must be rejected (storage abuse).
        huge = "org/" + ("x" * 5000)
        st, _ = _create(mr.InMemoryModelRequestStore(), _FakeTV("a", "a@t.test"),
                        json.dumps({"hf_repo_id": huge}), _config())
        self.assertEqual(st, HTTPStatus.BAD_REQUEST)

    def test_revision_with_dotdot_rejected(self):
        st, _ = _create(mr.InMemoryModelRequestStore(), _FakeTV("a", "a@t.test"),
                        '{"hf_repo_id":"meta-llama/x","revision":"main/../../etc"}', _config())
        self.assertEqual(st, HTTPStatus.BAD_REQUEST)

    def test_rate_limited_returns_429(self):
        from app.control_plane.agent_tools import RateLimiter
        rl = RateLimiter(per_minute=1, max_concurrency=2)
        store, tv = mr.InMemoryModelRequestStore(), _FakeTV("a", "a@t.test")
        st1, _ = _rl_create(store, tv, _config(), rl)
        st2, pl2 = _rl_create(store, tv, _config(), rl)
        self.assertEqual(st1, HTTPStatus.ACCEPTED)
        self.assertEqual(st2, HTTPStatus.TOO_MANY_REQUESTS)
        self.assertEqual(pl2["error"], "rate_limited")

    def test_open_request_cap(self):
        store = mr.InMemoryModelRequestStore()
        tv = _FakeTV("a", "a@t.test")
        for _ in range(2):
            _create(store, tv, '{"hf_repo_id":"meta-llama/x"}', _config(cap=2))
        st, pl = _create(store, tv, '{"hf_repo_id":"meta-llama/y"}', _config(cap=2))
        self.assertEqual(st, HTTPStatus.TOO_MANY_REQUESTS)


class IsolationTests(TestCase):
    def _seed_alice(self):
        store = mr.InMemoryModelRequestStore()
        _create(store, _FakeTV("alice", "alice@t-a.test"),
                '{"hf_repo_id":"meta-llama/x"}', _config(), _FakeNotif())
        return store

    def test_other_tenant_user_sees_nothing(self):
        store = self._seed_alice()
        st, pl = _list(store, _FakeTV("bob", "bob@t-b.test"), _config())
        self.assertEqual(st, HTTPStatus.OK)
        self.assertEqual(pl["count"], 0)
        self.assertFalse(pl["is_admin"])

    def test_same_tenant_other_user_sees_nothing(self):
        # A colleague in the SAME tenant but a different user must not see it.
        store = self._seed_alice()
        _st, pl = _list(store, _FakeTV("carol", "carol@t-a.test"), _config())
        self.assertEqual(pl["count"], 0)

    def test_owner_sees_own(self):
        store = self._seed_alice()
        _st, pl = _list(store, _FakeTV("alice", "alice@t-a.test"), _config())
        self.assertEqual(pl["count"], 1)

    def test_admin_sees_all_with_tenant(self):
        store = self._seed_alice()
        _st, pl = _list(store, _FakeTV("ops", "ops@t-z.test", groups=["admin"]), _config())
        self.assertEqual(pl["count"], 1)
        self.assertTrue(pl["is_admin"])
        self.assertIn("tenant_id", pl["requests"][0])
        self.assertIn("requested_by", pl["requests"][0])


class UpdateTests(TestCase):
    def _seed(self):
        store, notif = mr.InMemoryModelRequestStore(), _FakeNotif()
        _st, pl = _create(store, _FakeTV("alice", "alice@t-a.test"),
                          '{"hf_repo_id":"meta-llama/x"}', _config(), notif)
        return store, pl["id"], notif

    def test_non_admin_cannot_update(self):
        store, rid, _ = self._seed()
        st, pl = mr.build_model_request_update_response(
            authorization="Bearer y", request_id=rid, body='{"status":"approved"}',
            token_verifier=_FakeTV("bob", "bob@t-b.test"), store=store, config=_config(),
        )
        self.assertEqual(st, HTTPStatus.FORBIDDEN)
        # unchanged
        self.assertEqual(store.get(request_id=rid).status, "requested")

    def test_admin_updates_and_notifies_owner(self):
        store, rid, _ = self._seed()
        notif = _FakeNotif()
        st, pl = mr.build_model_request_update_response(
            authorization="Bearer z", request_id=rid, body='{"status":"approved"}',
            token_verifier=_FakeTV("ops", "ops@t-z.test", groups=["admin"]),
            store=store, config=_config(), notification_store=notif,
        )
        self.assertEqual(st, HTTPStatus.OK)
        self.assertEqual(pl["status"], "approved")
        # Owner (alice / t-a.test) is notified — not the operator's tenant.
        self.assertEqual(len(notif.events), 1)
        self.assertEqual(notif.events[0].tenant_id, "t-a.test")
        self.assertEqual(notif.events[0].user_id, "alice")
        self.assertEqual(notif.events[0].event_class, "model_install_updated")

    def test_admin_update_bad_status_rejected(self):
        store, rid, _ = self._seed()
        st, _ = mr.build_model_request_update_response(
            authorization="Bearer z", request_id=rid, body='{"status":"pwned"}',
            token_verifier=_FakeTV("ops", "ops@t.test", groups=["admin"]),
            store=store, config=_config(),
        )
        self.assertEqual(st, HTTPStatus.BAD_REQUEST)


class ConfigTests(TestCase):
    def test_config_parses_kill_switch_and_allowlist(self):
        from app.control_plane.config import ControlPlaneConfig
        c = ControlPlaneConfig.from_env({
            "MODEL_INSTALL_ENABLED": "true",
            "MODEL_INSTALL_ALLOWLIST": "meta-llama,mistralai",
        })
        self.assertTrue(c.model_install_enabled)
        self.assertEqual(c.model_install_allowlist, "meta-llama,mistralai")

    def test_config_defaults_deny(self):
        from app.control_plane.config import ControlPlaneConfig
        c = ControlPlaneConfig.from_env({})
        self.assertFalse(c.model_install_enabled)
        self.assertIsNone(c.model_install_allowlist)
