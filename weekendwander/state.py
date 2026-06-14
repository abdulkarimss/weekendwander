"""Tiny SQLite state store so the same deal isn't notified twice."""
import sqlite3
import time
from pathlib import Path


class State:
    def __init__(self, path="weekendwander_state.db", ttl_days=14):
        self.path = str(Path(path))
        self.ttl = ttl_days * 86400
        self.con = sqlite3.connect(self.path)
        self.con.execute(
            "CREATE TABLE IF NOT EXISTS seen "
            "(key TEXT PRIMARY KEY, price REAL, ts INTEGER)"
        )
        self.con.commit()
        self._purge()

    def _purge(self):
        cutoff = int(time.time()) - self.ttl
        self.con.execute("DELETE FROM seen WHERE ts < ?", (cutoff,))
        self.con.commit()

    @staticmethod
    def key(offer):
        return f"{offer['origin']}-{offer['destination']}-{offer['departure_at']}-{offer['return_at']}"

    def is_new(self, offer):
        """New if unseen, or seen before but now cheaper."""
        k = self.key(offer)
        row = self.con.execute("SELECT price FROM seen WHERE key=?", (k,)).fetchone()
        if row is None:
            return True
        return offer["price"] < row[0] - 0.01

    def remember(self, offer):
        self.con.execute(
            "INSERT INTO seen(key, price, ts) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET price=excluded.price, ts=excluded.ts",
            (self.key(offer), offer["price"], int(time.time())),
        )
        self.con.commit()

    def close(self):
        self.con.close()
