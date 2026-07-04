"""Tests for M11 deep-research (plan -> retrieve -> synthesize).

Against stub inference (no GPU) + a stub tenant-scoped retrieval store: auth,
kill-switch, cold-refuse, deny-by-default, validation, rate limit, the happy
path with citations, plan fallback, per-tenant retrieval isolation, budget and
inference-failure handling, and shape-only notifications.
"""
import json
import unittest

from app.control_plane.deep_research import (
    DeepResearchBudgets,
    build_deep_research_response,
    run_deep_research,
)
from app.control_plane.agent_tools import RateLimiter, parse_allowlist
from app.control_plane.embeddings import DeterministicEmbeddingClient
from app.control_plane.notifications import InMemoryNotificationStore
from app.control_plane.retrieval import RetrievedPassage
from app.control_plane.token_verifier import TokenClaims, TokenVerificationError


class _Verifier:
    def __init__(self, email="alice@tenant-a.test"):
        self._claims = TokenClaims(subject="user-a", email=email)

    def verify(self, raw):
        if raw != "valid":
            raise TokenVerificationError("bad")
        return self._claims


_ALICE = _Verifier("alice@tenant-a.test")
_BOB = _Verifier("bob@tenant-b.test")
_ALLOW = parse_allowlist(json.dumps({"tenant-a.test": ["deep_research"]}))
_BUDGETS = DeepResearchBudgets(max_subqueries=3, top_k=3, wall_clock_seconds=30, max_tokens=64, model="test")
_PLAN = '{"queries": ["kubernetes pods", "autoscaling"]}'
_SYNTH = "Pods autoscale via the HPA [1]."


def _passage(doc, chunk, text, score=0.9):
    return RetrievedPassage(document_id=doc, chunk_id=chunk, chunk_index=0, content=text, score=score)


class _ScriptedInference:
    def __init__(self, scripts):
        self._s = list(scripts)
        self.calls = []

    def chat_completions(self, request):
        self.calls.append(request)
        i = min(len(self.calls) - 1, len(self._s) - 1)
        return {"choices": [{"message": {"content": self._s[i]}}], "usage": {"total_tokens": 5}}


class _RaisingInference:
    def chat_completions(self, request):
        raise RuntimeError("inference down")


class _StubStore:
    def __init__(self, by_tenant):
        self._by = by_tenant
        self.queried_tenants = []

    def index_document(self, *a, **k):  # pragma: no cover - unused
        pass

    def query(self, *, tenant_id, embedding, top_k):
        self.queried_tenants.append(tenant_id)
        return self._by.get(tenant_id, [])[:top_k]


_DOCS = {"tenant-a.test": [_passage("doc-A", "c1", "pods scale via HPA"),
                           _passage("doc-A", "c2", "horizontal pod autoscaler")]}


def _invoke(verifier=_ALICE, enabled=True, allowlist=None, body=None,
            store=None, inference=None, rate_limiter=None, notes=None):
    return build_deep_research_response(
        authorization="Bearer valid",
        body=body if body is not None else json.dumps({"question": "how do pods autoscale?"}).encode(),
        token_verifier=verifier, enabled=enabled,
        allowlist=_ALLOW if allowlist is None else allowlist,
        store=store or _StubStore(_DOCS), embedding_client=DeterministicEmbeddingClient(),
        inference_client=inference if inference is not None else _ScriptedInference([_PLAN, _SYNTH]),
        budgets=_BUDGETS, rate_limiter=rate_limiter or RateLimiter(), notification_store=notes,
    )


class TestGating(unittest.TestCase):
    def test_requires_auth(self):
        status, _ = build_deep_research_response(
            authorization=None, body=b"{}", token_verifier=_ALICE, enabled=True,
            allowlist=_ALLOW, store=_StubStore({}), embedding_client=DeterministicEmbeddingClient(),
            inference_client=_ScriptedInference([_PLAN]), budgets=_BUDGETS, rate_limiter=RateLimiter())
        self.assertEqual(status, 401)

    def test_kill_switch(self):
        self.assertEqual(_invoke(enabled=False)[0], 503)

    def test_cold_inference_refuses(self):
        status, payload = build_deep_research_response(
            authorization="Bearer valid", body=json.dumps({"question": "x"}).encode(),
            token_verifier=_ALICE, enabled=True, allowlist=_ALLOW, store=_StubStore({}),
            embedding_client=DeterministicEmbeddingClient(), inference_client=None,
            budgets=_BUDGETS, rate_limiter=RateLimiter())
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], "research_unavailable")

    def test_deny_by_default(self):
        # Bob's tenant is not allow-listed for deep_research.
        status, payload = _invoke(verifier=_BOB)
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"], "not_allowed")

    def test_bad_json(self):
        self.assertEqual(_invoke(body=b"not json")[0], 400)

    def test_missing_question(self):
        self.assertEqual(_invoke(body=json.dumps({"question": "   "}).encode())[0], 400)

    def test_question_too_large(self):
        self.assertEqual(_invoke(body=json.dumps({"question": "x" * 5000}).encode())[0], 413)

    def test_rate_limit(self):
        rl = RateLimiter(per_minute=1, max_concurrency=4)
        self.assertEqual(_invoke(rate_limiter=rl)[0], 200)
        s2, p2 = _invoke(rate_limiter=rl)
        self.assertEqual(s2, 429)
        self.assertEqual(p2["error"], "rate_limited")


class TestResearch(unittest.TestCase):
    def test_happy_path_with_citations(self):
        status, payload = _invoke()
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["answer"], _SYNTH)
        self.assertEqual(payload["sources"], ["doc-A"])      # cited document ids
        self.assertEqual(payload["subqueries"], 2)

    def test_plan_fallback_on_garbage(self):
        # Plan returns garbage → falls back to the question itself (1 sub-query).
        inf = _ScriptedInference(["not json at all", _SYNTH])
        status, payload = _invoke(inference=inf)
        self.assertEqual(status, 200)
        self.assertEqual(payload["subqueries"], 1)

    def test_retrieval_is_tenant_scoped(self):
        store = _StubStore(_DOCS)
        _invoke(store=store)
        # Every retrieval call was scoped to the caller's tenant — no bypass.
        self.assertTrue(store.queried_tenants)
        self.assertTrue(all(t == "tenant-a.test" for t in store.queried_tenants))

    def test_no_passages_still_completes(self):
        status, payload = _invoke(store=_StubStore({}))   # empty corpus
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["sources"], [])

    def test_inference_failure_fails(self):
        status, payload = _invoke(inference=_RaisingInference())
        self.assertEqual(status, 502)
        self.assertEqual(payload["status"], "failed")

    def test_wall_clock_budget(self):
        budgets = DeepResearchBudgets(max_subqueries=3, top_k=3, wall_clock_seconds=0.0,
                                      max_tokens=64, model="test")
        out = run_deep_research(
            question="q", tenant_id="tenant-a.test", user_id="u",
            store=_StubStore(_DOCS), embedding_client=DeterministicEmbeddingClient(),
            inference_client=_ScriptedInference([_PLAN, _SYNTH]), budgets=budgets)
        self.assertEqual(out.status, "failed")
        self.assertEqual(out.detail, "wall_clock")


class TestNotificationsAndPolicy(unittest.TestCase):
    def test_progress_and_completed_emitted(self):
        notes = InMemoryNotificationStore()
        _invoke(notes=notes)
        feed = notes.list_for_user(tenant_id="tenant-a.test", user_id="user-a")
        self.assertEqual(sorted(n.event_class for n in feed),
                         ["agent_task_completed", "agent_task_progress"])

    def test_notification_carries_no_question(self):
        notes = InMemoryNotificationStore()
        secret = "SECRET-RESEARCH-QUESTION"
        _invoke(body=json.dumps({"question": secret}).encode(), notes=notes)
        feed = notes.list_for_user(tenant_id="tenant-a.test", user_id="user-a")
        for n in feed:
            self.assertNotIn(secret, n.resource_id)


class _FakeWebClient:
    def __init__(self, results):
        self._results = results
        self.queries = []

    def search(self, query):
        self.queries.append(query)
        return self._results


class TestWebResearch(unittest.TestCase):
    def _web_result(self, url="https://ex/pods", title="Docs", snippet="hpa scales pods"):
        from app.control_plane.web_search import WebResult
        return WebResult(title=title, url=url, snippet=snippet)

    def test_web_results_added_as_cited_sources(self):
        web = _FakeWebClient([self._web_result()])
        out = run_deep_research(
            question="how do pods autoscale?", tenant_id="tenant-a.test", user_id="u",
            store=_StubStore(_DOCS), embedding_client=DeterministicEmbeddingClient(),
            inference_client=_ScriptedInference([_PLAN, _SYNTH]), budgets=_BUDGETS,
            use_web=True, web_search_client=web)
        self.assertEqual(out.status, "completed")
        self.assertIn("https://ex/pods", out.sources)  # web URL cited alongside corpus docs
        self.assertTrue(web.queries)                    # the web client was actually queried

    def test_web_flag_without_client_is_corpus_only(self):
        out = run_deep_research(
            question="q", tenant_id="tenant-a.test", user_id="u",
            store=_StubStore(_DOCS), embedding_client=DeterministicEmbeddingClient(),
            inference_client=_ScriptedInference([_PLAN, _SYNTH]), budgets=_BUDGETS,
            use_web=True, web_search_client=None)
        self.assertEqual(out.status, "completed")   # no client -> deny-by-default, still completes

    def test_handler_web_flag_needs_configured_client(self):
        # web:true in the body but no client configured -> corpus-only, no error.
        status, _ = build_deep_research_response(
            authorization="Bearer valid",
            body=json.dumps({"question": "q", "web": True}).encode(),
            token_verifier=_ALICE, enabled=True, allowlist=_ALLOW, store=_StubStore(_DOCS),
            embedding_client=DeterministicEmbeddingClient(),
            inference_client=_ScriptedInference([_PLAN, _SYNTH]), budgets=_BUDGETS,
            rate_limiter=RateLimiter(), web_search_client=None)
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
