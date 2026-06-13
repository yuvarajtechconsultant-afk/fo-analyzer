"""
Lightweight SQLite store for option-chain snapshots.

Captures the ATM straddle premium, ATM IV, and OI/PCR at intervals so the
app can show real intraday straddle behaviour and an IV percentile / IV-crush
warning over time. Falls back gracefully when there is little/no history.
"""
import os
import sqlite3
import logging
from datetime import datetime, date
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "snapshots.db")

# Minimum seconds between stored snapshots per index (throttle).
_MIN_INTERVAL_SEC = 150
_last_record: Dict[str, datetime] = {}


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=5)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    try:
        with _conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS chain_snapshots (
                    ts          TEXT,
                    day         TEXT,
                    idx         TEXT,
                    spot        REAL,
                    atm         REAL,
                    ce_ltp      REAL,
                    pe_ltp      REAL,
                    straddle    REAL,
                    atm_iv      REAL,
                    total_ce_oi REAL,
                    total_pe_oi REAL,
                    pcr         REAL
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS ix_snap_day_idx ON chain_snapshots(day, idx)")
    except Exception as e:
        logger.warning("snapshot init_db failed: %s", e)


def record(index: str, data: Dict[str, Any], force: bool = False) -> bool:
    """Append a snapshot row (throttled per index). Returns True if stored."""
    index = index.upper()
    now = datetime.now()
    last = _last_record.get(index)
    if not force and last and (now - last).total_seconds() < _MIN_INTERVAL_SEC:
        return False
    try:
        with _conn() as c:
            c.execute("""
                INSERT INTO chain_snapshots
                (ts, day, idx, spot, atm, ce_ltp, pe_ltp, straddle,
                 atm_iv, total_ce_oi, total_pe_oi, pcr)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                now.isoformat(), now.strftime("%Y-%m-%d"), index,
                data.get("spot"), data.get("atm"),
                data.get("ce_ltp"), data.get("pe_ltp"), data.get("straddle"),
                data.get("atm_iv"), data.get("total_ce_oi"),
                data.get("total_pe_oi"), data.get("pcr"),
            ))
        _last_record[index] = now
        return True
    except Exception as e:
        logger.warning("snapshot record failed for %s: %s", index, e)
        return False


def today_series(index: str) -> List[Dict[str, Any]]:
    """All snapshots stored today for an index, oldest first."""
    index = index.upper()
    today = date.today().strftime("%Y-%m-%d")
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT * FROM chain_snapshots WHERE day=? AND idx=? ORDER BY ts",
                (today, index),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("snapshot today_series failed: %s", e)
        return []


def atm_iv_history(index: str, days: int = 60) -> List[float]:
    """Last stored ATM IV per day (for IV-percentile), most recent last."""
    index = index.upper()
    try:
        with _conn() as c:
            rows = c.execute("""
                SELECT day, atm_iv FROM chain_snapshots
                WHERE idx=? AND atm_iv IS NOT NULL
                GROUP BY day HAVING ts = MAX(ts)
                ORDER BY day DESC LIMIT ?
            """, (index, days)).fetchall()
        return [float(r["atm_iv"]) for r in reversed(rows)]
    except Exception as e:
        logger.warning("snapshot atm_iv_history failed: %s", e)
        return []


def iv_percentile(index: str, current_iv: float) -> Optional[float]:
    """Percentile rank of current IV within stored daily history (0-100)."""
    hist = atm_iv_history(index)
    if len(hist) < 5:
        return None
    below = sum(1 for v in hist if v <= current_iv)
    return round(below / len(hist) * 100, 1)
