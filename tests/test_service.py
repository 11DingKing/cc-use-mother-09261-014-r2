import os
import tempfile
import unittest

from service_09261_014.api import dispatch
from service_09261_014.store import SQLiteStore, VIEW_STATES
from service_09261_014.workflow import Workflow


class TestFlow(unittest.TestCase):
    def test_flow(self):
        f = Workflow()
        self.assertEqual(f.create("c1", "a", "k").state, "draft")
        self.assertEqual(f.create("c1", "a", "k").version, 1)
        self.assertEqual(f.move("c1", "reviewing", "b").state, "reviewing")

    def test_move_is_idempotent(self):
        f = Workflow()
        f.create("c1", "a")
        self.assertEqual(f.move("c1", "reviewing", "b").version, 2)
        again = f.move("c1", "reviewing", "b")
        self.assertEqual((again.state, again.version), ("reviewing", 2))

    def test_invalid_transition(self):
        f = Workflow()
        f.create("c1", "a")
        with self.assertRaises(ValueError):
            f.move("c1", "approved", "b")


class TestViews(unittest.TestCase):
    def setUp(self):
        # 覆盖全部状态：draft/reviewing/rejected→待办，approved/archived→已处理，cancelled→异常
        self.f = Workflow()
        self.f.create("c1", "a")                                               # draft
        self.f.create("c2", "a"); self.f.move("c2", "reviewing", "b")          # reviewing
        self.f.create("c3", "a"); self.f.move("c3", "reviewing", "b")
        self.f.move("c3", "rejected", "b")                                     # rejected
        self.f.create("c4", "a"); self.f.move("c4", "reviewing", "b")
        self.f.move("c4", "approved", "b")                                     # approved
        self.f.create("c5", "a"); self.f.move("c5", "reviewing", "b")
        self.f.move("c5", "approved", "b"); self.f.move("c5", "archived", "b") # archived
        self.f.create("c6", "a"); self.f.move("c6", "cancelled", "b")          # cancelled

    def test_counts_reconcile_with_views(self):
        counts = self.f.counts()
        self.assertEqual(counts, {"todo": 3, "processed": 2, "exception": 1, "total": 6})
        self.assertEqual(counts["todo"] + counts["processed"] + counts["exception"],
                         counts["total"])
        for view in VIEW_STATES:
            self.assertEqual(self.f.query(view=view)["total"], counts[view])

    def test_record_in_exactly_one_view(self):
        seen = []
        for view in VIEW_STATES:
            seen += [i["id"] for i in self.f.query(view=view, page_size=200)["items"]]
        self.assertEqual(sorted(seen), ["c1", "c2", "c3", "c4", "c5", "c6"])

    def test_processing_moves_record_between_views(self):
        self.f.move("c2", "approved", "b")
        self.assertEqual(self.f.counts()["todo"], 2)
        self.assertEqual(self.f.counts()["processed"], 3)
        todo_ids = [i["id"] for i in self.f.query(view="todo", page_size=200)["items"]]
        done_ids = [i["id"] for i in self.f.query(view="processed", page_size=200)["items"]]
        self.assertNotIn("c2", todo_ids)
        self.assertIn("c2", done_ids)

    def test_pagination_no_dup_no_miss(self):
        ids = []
        for page in (1, 2, 3, 4):
            ids += [i["id"] for i in self.f.query(page=page, page_size=2)["items"]]
        self.assertEqual(ids, ["c1", "c2", "c3", "c4", "c5", "c6"])
        todo = []
        for page in (1, 2, 3):
            todo += [i["id"] for i in self.f.query(view="todo", page=page, page_size=1)["items"]]
        self.assertEqual(sorted(todo), ["c1", "c2", "c3"])

    def test_empty_result_stable_shape(self):
        r = self.f.query(view="todo", actor="nobody", page=3, page_size=5)
        self.assertEqual(r, {"items": [], "total": 0, "page": 3, "page_size": 5})
        empty = Workflow()
        self.assertEqual(empty.query(), {"items": [], "total": 0, "page": 1, "page_size": 20})
        self.assertEqual(empty.counts(), {"todo": 0, "processed": 0, "exception": 0, "total": 0})

    def test_filters(self):
        r = self.f.query(view="todo", state="rejected")
        self.assertEqual([i["id"] for i in r["items"]], ["c3"])
        self.assertEqual(r["total"], 1)
        with self.assertRaises(ValueError):
            self.f.query(view="bogus")
        with self.assertRaises(ValueError):
            self.f.query(page=0)


class TestRestart(unittest.TestCase):
    def test_restart_keeps_order_and_counts(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "cases.db")
            f1 = Workflow(SQLiteStore(path))
            for i in range(7):
                f1.create("c%d" % i, "a", key="k%d" % i)
            f1.move("c1", "reviewing", "b")
            f1.move("c2", "reviewing", "b"); f1.move("c2", "approved", "b")
            f1.move("c3", "cancelled", "b")
            before_counts = f1.counts()
            before_pages = [[i["id"] for i in f1.query(page=p, page_size=3)["items"]]
                            for p in (1, 2, 3)]

            f2 = Workflow(SQLiteStore(path))  # 模拟重启：同一持久化文件重新加载
            self.assertEqual(f2.counts(), before_counts)
            after_pages = [[i["id"] for i in f2.query(page=p, page_size=3)["items"]]
                           for p in (1, 2, 3)]
            self.assertEqual(after_pages, before_pages)
            # 幂等键重启后仍生效，不产生重复记录
            f2.create("c0", "a", key="k0")
            self.assertEqual(f2.counts()["total"], 7)


class TestApi(unittest.TestCase):
    def setUp(self):
        self.f = Workflow()
        self.f.create("c1", "a")
        self.f.create("c2", "a")
        self.f.move("c2", "reviewing", "b")
        self.f.move("c2", "approved", "b")

    def test_get_cases_envelope(self):
        status, payload = dispatch(self.f, "GET", "/cases?view=todo&page=1&page_size=1")
        self.assertEqual(status, 200)
        self.assertEqual(payload["total"], 1)
        self.assertEqual([i["id"] for i in payload["items"]], ["c1"])
        self.assertEqual(payload["items"][0]["view"], "todo")

    def test_get_cases_empty_stable_shape(self):
        status, payload = dispatch(self.f, "GET", "/cases?view=exception")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"items": [], "total": 0, "page": 1, "page_size": 20})

    def test_counts_endpoint(self):
        status, payload = dispatch(self.f, "GET", "/cases/counts")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"todo": 1, "processed": 1, "exception": 0, "total": 2})

    def test_bad_view_and_pagination(self):
        self.assertEqual(dispatch(self.f, "GET", "/cases?view=bogus")[0], 400)
        self.assertEqual(dispatch(self.f, "GET", "/cases?page=x")[0], 400)

    def test_post_move_and_errors(self):
        status, payload = dispatch(self.f, "POST", "/cases",
                                   {"id": "c3", "actor": "a", "idempotency_key": "k3"})
        self.assertEqual(status, 201)
        self.assertEqual(payload["view"], "todo")
        self.assertEqual(dispatch(self.f, "POST", "/cases", {"id": "c3", "actor": "a"})[0], 409)
        self.assertEqual(dispatch(self.f, "POST", "/cases", {"id": "c4"})[0], 400)
        status, payload = dispatch(self.f, "POST", "/cases/c3/move",
                                   {"state": "reviewing", "actor": "b"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["state"], "reviewing")
        self.assertEqual(dispatch(self.f, "POST", "/cases/c3/move",
                                  {"state": "archived", "actor": "b"})[0], 409)
        self.assertEqual(dispatch(self.f, "POST", "/cases/nope/move",
                                  {"state": "reviewing", "actor": "b"})[0], 404)
        self.assertEqual(dispatch(self.f, "DELETE", "/cases")[0], 404)


if __name__ == "__main__":
    unittest.main()
