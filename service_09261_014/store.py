"""SQLite 状态仓储：筛选、分类口径与计数唯一的持久化数据源。"""
import sqlite3

# 分类口径：每个状态只属于一个视图，列表筛选与各类计数共用同一份映射，
# 因此同一批记录在三个视图中的数量必然对得上。
VIEW_STATES = {
    "todo": ("draft", "reviewing", "rejected"),   # 待办：仍需处理动作
    "processed": ("approved", "archived"),        # 已处理：处理动作已完成
    "exception": ("cancelled",),                  # 异常：被取消的终止态
}
STATE_VIEW = {state: view for view, states in VIEW_STATES.items() for state in states}

_COLUMNS = "id,actor,state,version,idem_key,seq"


class SQLiteStore:
    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS cases("
            "id TEXT PRIMARY KEY,"
            "actor TEXT NOT NULL,"
            "state TEXT NOT NULL,"
            "version INTEGER NOT NULL,"
            "idem_key TEXT UNIQUE,"
            "seq INTEGER NOT NULL)"
        )
        self.db.commit()

    @staticmethod
    def _row(r):
        return {"id": r[0], "actor": r[1], "state": r[2], "version": r[3],
                "idempotency_key": r[4], "seq": r[5]}

    def insert(self, row):
        # seq 单调递增且持久化，是跨重启稳定的排序键。
        seq = self.db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM cases").fetchone()[0]
        self.db.execute(
            "INSERT INTO cases(id,actor,state,version,idem_key,seq) VALUES(?,?,?,?,?,?)",
            (row["id"], row["actor"], row["state"], row["version"], row.get("idempotency_key"), seq),
        )
        self.db.commit()
        return self.get(row["id"])

    def update(self, row):
        self.db.execute(
            "UPDATE cases SET actor=?,state=?,version=? WHERE id=?",
            (row["actor"], row["state"], row["version"], row["id"]),
        )
        self.db.commit()

    def get(self, id):
        r = self.db.execute("SELECT " + _COLUMNS + " FROM cases WHERE id=?", (id,)).fetchone()
        return self._row(r) if r else None

    def get_by_key(self, key):
        r = self.db.execute("SELECT " + _COLUMNS + " FROM cases WHERE idem_key=?", (key,)).fetchone()
        return self._row(r) if r else None

    def all(self):
        return [self._row(r) for r in self.db.execute(
            "SELECT " + _COLUMNS + " FROM cases ORDER BY seq ASC,id ASC").fetchall()]

    @staticmethod
    def _where(view, state, actor):
        clauses, params = [], []
        if view is not None:
            states = VIEW_STATES.get(view)
            if states is None:
                raise ValueError("unknown view")
            clauses.append("state IN (" + ",".join("?" * len(states)) + ")")
            params.extend(states)
        if state is not None:
            clauses.append("state=?")
            params.append(state)
        if actor is not None:
            clauses.append("actor=?")
            params.append(actor)
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params

    def query(self, view=None, state=None, actor=None, page=1, page_size=20):
        # 列表与 total 用同一条 WHERE 从同一张表算出，不会相互漂移。
        where, params = self._where(view, state, actor)
        total = self.db.execute("SELECT COUNT(*) FROM cases" + where, params).fetchone()[0]
        rows = self.db.execute(
            "SELECT " + _COLUMNS + " FROM cases" + where
            + " ORDER BY seq ASC,id ASC LIMIT ? OFFSET ?",
            params + [page_size, (page - 1) * page_size],
        ).fetchall()
        # 空结果同样返回稳定结构：items 恒为列表、total 恒为整数。
        return {"items": [self._row(r) for r in rows], "total": total,
                "page": page, "page_size": page_size}

    def counts(self):
        counts = {}
        for view, states in VIEW_STATES.items():
            counts[view] = self.db.execute(
                "SELECT COUNT(*) FROM cases WHERE state IN (" + ",".join("?" * len(states)) + ")",
                states,
            ).fetchone()[0]
        counts["total"] = self.db.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        return counts
