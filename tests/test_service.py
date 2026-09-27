import os
import tempfile
import unittest

from service_09261_014.api import dispatch
from service_09261_014.store import SQLiteStore
from service_09261_014.workflow import Workflow


class TestFlow(unittest.TestCase):
    def test_flow(self):
        f = Workflow()
        self.assertEqual(f.create("c1", "a", "k").state, "draft")
        self.assertEqual(f.create("c1", "a", "k").version, 1)
        self.assertEqual(f.move("c1", "reviewing", "b").state, "reviewing")


class TestStoreCompat(unittest.TestCase):
    def test_snapshots(self):
        store = SQLiteStore()
        self.assertEqual(store.latest(), [])
        store.save({"a": 1})
        store.save({"b": 2})
        self.assertEqual(store.latest(), {"b": 2})


# (id, 迁移路径)：todo=c1,c2,c3,c7  processed=c4,c5,c8  exception=c6
SEED = [
    ("c1", []),
    ("c2", ["reviewing"]),
    ("c3", ["reviewing", "rejected"]),
    ("c4", ["reviewing", "approved"]),
    ("c5", ["reviewing", "approved", "archived"]),
    ("c6", ["cancelled"]),
    ("c7", ["reviewing"]),
    ("c8", ["reviewing", "approved"]),
]
ALL_IDS = [cid for cid, _ in SEED]


def seed(flow):
    for case_id, path in SEED:
        flow.create(case_id, "ops")
        for state in path:
            flow.move(case_id, state, "ops")


class TestViews(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "cases.db")
        self.flow = Workflow(SQLiteStore(self.path))
        seed(self.flow)

    def tearDown(self):
        self.dir.cleanup()

    def view_ids(self, view):
        return [i["id"] for i in self.flow.query(view=view, page_size=100)["items"]]

    def test_counts_match_listing(self):
        for view in ("todo", "processed", "exception", "all"):
            result = self.flow.query(view=view, page_size=100)
            self.assertEqual(result["total"], len(result["items"]))
            if view != "all":
                self.assertEqual(result["total"], result["counts"][view])
        result = self.flow.query(view="all", page_size=100)
        counts = result["counts"]
        self.assertEqual((counts["todo"], counts["processed"], counts["exception"]), (4, 3, 1))
        self.assertEqual(counts["todo"] + counts["processed"] + counts["exception"], counts["all"])
        self.assertEqual(result["total"], counts["all"])

    def test_each_case_in_exactly_one_view(self):
        seen = self.view_ids("todo") + self.view_ids("processed") + self.view_ids("exception")
        self.assertEqual(sorted(seen), ALL_IDS)

    def test_pagination_no_dup_no_missing(self):
        for view in ("all", "todo", "processed", "exception"):
            ids, page = [], 1
            while True:
                result = self.flow.query(view=view, page=page, page_size=3)
                self.assertEqual(result["pages"], -(-result["total"] // 3))
                ids += [i["id"] for i in result["items"]]
                if page >= result["pages"]:
                    break
                page += 1
            self.assertEqual(len(ids), len(set(ids)), view)
            self.assertEqual(ids, self.view_ids(view), view)

    def test_empty_result_keeps_structure(self):
        result = self.flow.query(view="exception", actor="nobody")
        self.assertEqual(result["items"], [])
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["pages"], 0)
        self.assertEqual(result["counts"], {"todo": 0, "processed": 0, "exception": 0, "all": 0})
        for key in ("view", "page", "page_size", "total", "pages", "counts", "items"):
            self.assertIn(key, result)

    def test_move_lands_in_correct_view_only(self):
        self.flow.move("c2", "approved", "ops")
        self.assertNotIn("c2", self.view_ids("todo"))
        self.assertIn("c2", self.view_ids("processed"))
        self.assertNotIn("c2", self.view_ids("exception"))
        counts = self.flow.query()["counts"]
        self.assertEqual((counts["todo"], counts["processed"], counts["exception"]), (3, 4, 1))

    def test_filters_apply_to_items_and_counts(self):
        self.flow.create("c9", "alice")
        result = self.flow.query(actor="alice")
        self.assertEqual([i["id"] for i in result["items"]], ["c9"])
        self.assertEqual(result["counts"]["all"], 1)
        result = self.flow.query(view="todo", state="draft")
        self.assertEqual({i["state"] for i in result["items"]}, {"draft"})
        self.assertEqual(result["total"], result["counts"]["todo"])

    def test_invalid_params(self):
        with self.assertRaises(ValueError):
            self.flow.query(view="bogus")
        with self.assertRaises(ValueError):
            self.flow.query(state="bogus")

    def test_restart_keeps_order_counts_and_idempotency(self):
        self.flow.create("k1", "ops", "key-1")
        self.flow.move("k1", "reviewing", "ops")
        before1 = self.flow.query(view="all", page=1, page_size=5)["items"]
        before2 = self.flow.query(view="all", page=2, page_size=5)["items"]
        before_counts = self.flow.query()["counts"]
        reopened = Workflow(SQLiteStore(self.path))
        self.assertEqual(reopened.query(view="all", page=1, page_size=5)["items"], before1)
        self.assertEqual(reopened.query(view="all", page=2, page_size=5)["items"], before2)
        self.assertEqual(reopened.query()["counts"], before_counts)
        replayed = reopened.create("k1", "ops", "key-1")
        self.assertEqual((replayed.state, replayed.version), ("reviewing", 2))
        self.assertEqual(reopened.query()["counts"], before_counts)
        with self.assertRaises(ValueError):
            reopened.create("k1", "ops")


class TestApi(unittest.TestCase):
    def setUp(self):
        self.flow = Workflow()
        seed(self.flow)

    def test_get_cases_envelope(self):
        status, payload = dispatch(self.flow, "GET", "/cases?view=todo&page=1&page_size=2")
        self.assertEqual(status, 200)
        self.assertEqual(payload["view"], "todo")
        self.assertEqual([i["id"] for i in payload["items"]], ["c1", "c2"])
        self.assertEqual(payload["total"], 4)
        self.assertEqual(payload["pages"], 2)
        self.assertEqual(payload["counts"], {"todo": 4, "processed": 3, "exception": 1, "all": 8})

    def test_get_cases_empty_stable(self):
        status, payload = dispatch(self.flow, "GET", "/cases?actor=nobody")
        self.assertEqual(status, 200)
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["total"], 0)
        self.assertEqual(payload["counts"]["all"], 0)

    def test_invalid_view(self):
        status, _ = dispatch(self.flow, "GET", "/cases?view=bogus")
        self.assertEqual(status, 400)

    def test_post_move_roundtrip(self):
        status, payload = dispatch(self.flow, "POST", "/cases", {"id": "n1", "actor": "ops", "idempotency_key": "kk"})
        self.assertEqual((status, payload["state"]), (201, "draft"))
        again = dispatch(self.flow, "POST", "/cases", {"id": "n1", "actor": "ops", "idempotency_key": "kk"})
        self.assertEqual(again[1]["version"], 1)
        status, payload = dispatch(self.flow, "POST", "/cases/n1/move", {"state": "reviewing", "actor": "ops"})
        self.assertEqual((status, payload["state"], payload["version"]), (200, "reviewing", 2))
        self.assertIn("n1", [i["id"] for i in dispatch(self.flow, "GET", "/cases?view=todo")[1]["items"]])

    def test_error_mapping(self):
        self.assertEqual(dispatch(self.flow, "POST", "/cases", {"id": "c1", "actor": "ops"})[0], 409)
        self.assertEqual(dispatch(self.flow, "POST", "/cases/ghost/move", {"state": "reviewing", "actor": "ops"})[0], 404)
        self.assertEqual(dispatch(self.flow, "POST", "/cases/c1/move", {"state": "archived", "actor": "ops"})[0], 409)
        self.assertEqual(dispatch(self.flow, "POST", "/cases", {"id": "x"})[0], 400)
        self.assertEqual(dispatch(self.flow, "GET", "/nope")[0], 404)


if __name__ == "__main__":
    unittest.main()
