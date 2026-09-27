"""SQLite 状态仓储：案例、幂等键与查询都落在同一个数据库文件上。"""
import json
import sqlite3

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS snapshots("
    "id INTEGER PRIMARY KEY AUTOINCREMENT,body TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS cases("
    "seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    "id TEXT NOT NULL UNIQUE,"
    "actor TEXT NOT NULL,"
    "state TEXT NOT NULL,"
    "version INTEGER NOT NULL,"
    "created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),"
    "updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))",
    "CREATE TABLE IF NOT EXISTS idempotency_keys("
    "key TEXT PRIMARY KEY,"
    "case_id TEXT NOT NULL REFERENCES cases(id))",
)


def _case_row(row):
    return {"id": row[0], "actor": row[1], "state": row[2], "version": row[3]}


def _filters(view_states=None, state=None, actor=None):
    clauses, args = [], []
    if view_states is not None:
        clauses.append("state IN (" + ",".join("?" for _ in view_states) + ")")
        args.extend(view_states)
    if state is not None:
        clauses.append("state=?")
        args.append(state)
    if actor is not None:
        clauses.append("actor=?")
        args.append(actor)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", args


class SQLiteStore:
    def __init__(self, path=":memory:"):
        # 自动提交模式；多语句操作显式开事务，保证重启前后语义一致。
        self.db = sqlite3.connect(path, isolation_level=None)
        for ddl in _SCHEMA:
            self.db.execute(ddl)

    # ---- 通用快照（兼容保留） ----
    def save(self, value):
        self.db.execute("INSERT INTO snapshots(body) VALUES(?)", (json.dumps(value, ensure_ascii=False),))

    def latest(self):
        row = self.db.execute("SELECT body FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
        return json.loads(row[0]) if row else []

    # ---- 案例仓储 ----
    def case_id_for_key(self, key):
        row = self.db.execute("SELECT case_id FROM idempotency_keys WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def get_case(self, case_id):
        row = self.db.execute("SELECT id,actor,state,version FROM cases WHERE id=?", (case_id,)).fetchone()
        return _case_row(row) if row else None

    def insert_case(self, case, key=None):
        # 案例与幂等键同一事务写入，任何时刻都不会只落下一半。
        self.db.execute("BEGIN")
        try:
            self.db.execute(
                "INSERT INTO cases(id,actor,state,version) VALUES(?,?,?,?)",
                (case["id"], case["actor"], case["state"], case["version"]))
            if key:
                self.db.execute("INSERT INTO idempotency_keys(key,case_id) VALUES(?,?)", (key, case["id"]))
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def update_case(self, case_id, state, actor, version):
        # 单条 UPDATE 原子完成状态迁移；带旧版本号做乐观并发控制。
        cur = self.db.execute(
            "UPDATE cases SET state=?,actor=?,version=?,"
            "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=? AND version=?",
            (state, actor, version, case_id, version - 1))
        if cur.rowcount != 1:
            raise ValueError("conflict")

    def list_all(self):
        rows = self.db.execute("SELECT id,actor,state,version FROM cases ORDER BY id").fetchall()
        return [_case_row(r) for r in rows]

    def page(self, view_states=None, state=None, actor=None, limit=20, offset=0):
        """同一事务内返回 (items,total,state_counts)：列表、筛选与计数读取同一快照。

        排序键是持久化的自增 seq（入库顺序），唯一且不可变，
        因此翻页不会重复或漏项，重启后顺序不变。
        """
        where, args = _filters(view_states, state, actor)
        count_where, count_args = _filters(None, state, actor)
        self.db.execute("BEGIN")
        try:
            items = [_case_row(r) for r in self.db.execute(
                "SELECT id,actor,state,version FROM cases" + where + " ORDER BY seq ASC LIMIT ? OFFSET ?",
                (*args, limit, offset)).fetchall()]
            total = self.db.execute("SELECT COUNT(*) FROM cases" + where, args).fetchone()[0]
            state_counts = dict(self.db.execute(
                "SELECT state,COUNT(*) FROM cases" + count_where + " GROUP BY state", count_args).fetchall())
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return items, total, state_counts
