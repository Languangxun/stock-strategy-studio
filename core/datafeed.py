from __future__ import annotations

import contextlib
import io
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

import pandas as pd


class DataError(RuntimeError):
    pass


COLUMN_ALIASES = {
    "日期": "date",
    "date": "date",
    "开盘": "open",
    "open": "open",
    "收盘": "close",
    "close": "close",
    "最高": "high",
    "high": "high",
    "最低": "low",
    "low": "low",
    "成交量": "volume",
    "volume": "volume",
    "vol": "volume",
    "成交额": "amount",
    "amount": "amount",
}


@dataclass(frozen=True)
class Symbol:
    code: str
    market: str

    @property
    def full_code(self) -> str:
        return f"{self.market}{self.code}"


def parse_symbol(value: str) -> Symbol:
    raw = str(value or "").strip().lower().replace(" ", "")
    raw = raw.replace(".sh", "sh").replace(".sz", "sz").replace(".bj", "bj")
    for prefix in ("sh", "sz", "bj"):
        if raw.startswith(prefix):
            digits = "".join(ch for ch in raw[len(prefix):] if ch.isdigit())
            if len(digits) != 6:
                raise DataError(f"股票代码应为 6 位数字: {value}")
            return Symbol(digits, prefix)
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) != 6:
        raise DataError(f"股票代码应为 6 位数字: {value}")
    if digits.startswith(("60", "68", "69", "90")):
        market = "sh"
    elif digits.startswith(("43", "44", "81", "82", "83", "87", "88", "92")):
        market = "bj"
    else:
        market = "sz"
    return Symbol(digits, market)


def parse_index(value: str) -> str:
    raw = str(value or "").strip().lower().replace(" ", "")
    if raw.startswith(("sh", "sz", "bj")):
        digits = "".join(ch for ch in raw[2:] if ch.isdigit())
        return f"{raw[:2]}{digits.zfill(6)}"
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) != 6:
        raise DataError(f"指数代码应为 6 位数字: {value}")
    return ("sz" if digits.startswith("399") else "sh") + digits


def _norm_date(value: str | date | datetime | pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(value).normalize()


def _standardize(df: pd.DataFrame, source: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    if df is None or len(df) == 0:
        return pd.DataFrame()
    work = df.rename(columns={k: v for k, v in COLUMN_ALIASES.items() if k in df.columns})
    required = ["date", "open", "high", "low", "close", "volume"]
    if not all(col in work.columns for col in required):
        raise DataError(f"{source} 返回字段不完整: {list(df.columns)}")
    if "amount" not in work.columns:
        work["amount"] = 0.0
    out = work[["date", "open", "high", "low", "close", "volume", "amount"]].copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.normalize().astype("datetime64[ns]")
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["date", "open", "high", "low", "close"])
    out = out[out["close"] > 0]
    out = out[(out["date"] >= start) & (out["date"] <= end)]
    out = out.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    out["volume"] = out["volume"].fillna(0.0)
    out["amount"] = out["amount"].fillna(0.0)
    out.attrs["source"] = source
    return out


class CacheStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextlib.contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        try:
            yield con
        finally:
            con.close()

    def _init_db(self) -> None:
        with self.connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS bars(
                    symbol TEXT NOT NULL,
                    adjust TEXT NOT NULL,
                    date TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    amount REAL,
                    source TEXT,
                    updated_at TEXT,
                    PRIMARY KEY(symbol, adjust, date)
                );
                CREATE TABLE IF NOT EXISTS index_bars(
                    symbol TEXT NOT NULL,
                    date TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    amount REAL,
                    source TEXT,
                    updated_at TEXT,
                    PRIMARY KEY(symbol, date)
                );
                CREATE INDEX IF NOT EXISTS idx_bars_lookup ON bars(symbol, adjust, date);
                CREATE INDEX IF NOT EXISTS idx_index_lookup ON index_bars(symbol, date);
                """
            )

    def read_bars(self, symbol: str, adjust: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        with self.connect() as con:
            df = pd.read_sql_query(
                "SELECT date, open, high, low, close, volume, amount, source FROM bars "
                "WHERE symbol=? AND adjust=? AND date>=? AND date<=? ORDER BY date",
                con,
                params=(symbol, adjust, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")),
            )
        return self._finish_read(df, "cache")

    def coverage(self, symbol: str, adjust: str) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
        with self.connect() as con:
            row = con.execute(
                "SELECT MIN(date), MAX(date) FROM bars WHERE symbol=? AND adjust=?",
                (symbol, adjust),
            ).fetchone()
        if not row or not row[0]:
            return None, None
        return pd.Timestamp(row[0]), pd.Timestamp(row[1])

    def write_bars(self, symbol: str, adjust: str, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = [
            (
                symbol,
                adjust,
                pd.Timestamp(row.date).strftime("%Y-%m-%d"),
                float(row.open),
                float(row.high),
                float(row.low),
                float(row.close),
                float(row.volume),
                float(row.amount),
                str(df.attrs.get("source", "akshare")),
                now,
            )
            for row in df.itertuples(index=False)
        ]
        with self.connect() as con:
            con.executemany(
                "INSERT INTO bars(symbol, adjust, date, open, high, low, close, volume, amount, source, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(symbol, adjust, date) DO UPDATE SET "
                "open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close, "
                "volume=excluded.volume, amount=excluded.amount, source=excluded.source, "
                "updated_at=excluded.updated_at",
                rows,
            )
            con.commit()

    def read_index(self, symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        with self.connect() as con:
            df = pd.read_sql_query(
                "SELECT date, open, high, low, close, volume, amount, source FROM index_bars "
                "WHERE symbol=? AND date>=? AND date<=? ORDER BY date",
                con,
                params=(symbol, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")),
            )
        return self._finish_read(df, "cache")

    def index_coverage(self, symbol: str) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
        with self.connect() as con:
            row = con.execute(
                "SELECT MIN(date), MAX(date) FROM index_bars WHERE symbol=?",
                (symbol,),
            ).fetchone()
        if not row or not row[0]:
            return None, None
        return pd.Timestamp(row[0]), pd.Timestamp(row[1])

    def write_index(self, symbol: str, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = [
            (
                symbol,
                pd.Timestamp(row.date).strftime("%Y-%m-%d"),
                float(row.open),
                float(row.high),
                float(row.low),
                float(row.close),
                float(row.volume),
                float(row.amount),
                str(df.attrs.get("source", "akshare")),
                now,
            )
            for row in df.itertuples(index=False)
        ]
        with self.connect() as con:
            con.executemany(
                "INSERT INTO index_bars(symbol, date, open, high, low, close, volume, amount, source, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(symbol, date) DO UPDATE SET "
                "open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close, "
                "volume=excluded.volume, amount=excluded.amount, source=excluded.source, "
                "updated_at=excluded.updated_at",
                rows,
            )
            con.commit()

    def write_bulk(
        self,
        df: pd.DataFrame,
        progress: Callable[[int, int], None] | None = None,
        chunk_size: int = 50000,
    ) -> int:
        if df is None or df.empty:
            return 0
        work = df.copy()
        work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        work = work.dropna(subset=["date", "symbol", "adjust", "open", "high", "low", "close"])
        if work.empty:
            return 0
        for col in ("volume", "amount"):
            if col not in work.columns:
                work[col] = 0.0
            work[col] = pd.to_numeric(work[col], errors="coerce").fillna(0.0)
        work["source"] = work.get("source", pd.Series("import", index=work.index)).fillna("import").astype(str)
        work["adjust"] = work["adjust"].astype(str)
        work["symbol"] = work["symbol"].astype(str)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        columns = ["symbol", "adjust", "date", "open", "high", "low", "close", "volume", "amount", "source"]
        records = work[columns]
        total = len(records)
        processed = 0
        sql = (
            "INSERT INTO bars(symbol, adjust, date, open, high, low, close, volume, amount, source, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(symbol, adjust, date) DO UPDATE SET "
            "open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close, "
            "volume=excluded.volume, amount=excluded.amount, source=excluded.source, "
            "updated_at=excluded.updated_at"
        )
        with self.connect() as con:
            for start in range(0, total, chunk_size):
                chunk = records.iloc[start:start + chunk_size]
                rows = [
                    (
                        row.symbol,
                        row.adjust,
                        row.date,
                        float(row.open),
                        float(row.high),
                        float(row.low),
                        float(row.close),
                        float(row.volume),
                        float(row.amount),
                        row.source,
                        now,
                    )
                    for row in chunk.itertuples(index=False)
                ]
                con.executemany(sql, rows)
                con.commit()
                processed += len(rows)
                if progress:
                    progress(processed, total)
        return total

    def summary(self) -> dict[str, dict[str, object]]:
        with self.connect() as con:
            df = pd.read_sql_query(
                "SELECT adjust, COUNT(DISTINCT symbol) symbols, COUNT(*) bars, "
                "MIN(date) first_date, MAX(date) last_date FROM bars GROUP BY adjust",
                con,
            )
        return {
            str(row.adjust or ""): {
                "symbols": int(row.symbols),
                "bars": int(row.bars),
                "first_date": str(row.first_date or ""),
                "last_date": str(row.last_date or ""),
            }
            for row in df.itertuples(index=False)
        }

    def market_symbols(
        self,
        adjust: str,
        start: pd.Timestamp,
        end: pd.Timestamp,
        min_bars: int = 65,
        limit: int | None = None,
    ) -> pd.DataFrame:
        sql = (
            "SELECT symbol, COUNT(*) AS bars, MIN(date) AS first_date, MAX(date) AS last_date "
            "FROM bars WHERE adjust=? AND date>=? AND date<=? "
            "AND (symbol LIKE 'sh60%' OR symbol LIKE 'sh68%' OR symbol LIKE 'sz00%' "
            "OR symbol LIKE 'sz30%' OR symbol LIKE 'bj%') "
            "GROUP BY symbol HAVING COUNT(*)>=? ORDER BY bars DESC, symbol"
        )
        params: list[object] = [adjust, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"), int(min_bars)]
        if limit is not None and limit > 0:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self.connect() as con:
            return pd.read_sql_query(sql, con, params=params)

    @staticmethod
    def _finish_read(df: pd.DataFrame, source: str) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount"])
        df["date"] = pd.to_datetime(df["date"]).astype("datetime64[ns]")
        for col in ["open", "high", "low", "close", "volume", "amount"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["date", "open", "high", "low", "close"])
        df = df.sort_values("date").reset_index(drop=True)
        df.attrs["source"] = df["source"].iloc[-1] if "source" in df.columns and len(df) else source
        df.attrs["cached"] = True
        return df.drop(columns=["source"], errors="ignore")


class DataFeed:
    def __init__(
        self,
        cache_path: str | Path,
        sample_dir: str | Path | None = None,
        offline: bool = False,
        progress: Callable[[str], None] | None = None,
    ):
        self.cache = CacheStore(cache_path)
        self.sample_dir = Path(sample_dir) if sample_dir else None
        self.offline = offline
        self.progress = progress or (lambda _msg: None)

    def _log(self, message: str) -> None:
        self.progress(message)

    def load_stock(
        self,
        symbol: str,
        start: str | date | datetime,
        end: str | date | datetime,
        adjust: str = "qfq",
        allow_sample: bool = True,
    ) -> pd.DataFrame:
        sym = parse_symbol(symbol)
        start_ts = _norm_date(start)
        end_ts = _norm_date(end)
        if start_ts > end_ts:
            raise DataError("开始日期不能晚于结束日期")
        cached = self.cache.read_bars(sym.full_code, adjust, start_ts, end_ts)
        min_date, max_date = self.cache.coverage(sym.full_code, adjust)
        covered = bool(
            min_date is not None
            and max_date is not None
            and min_date <= start_ts + timedelta(days=7)
            and max_date >= end_ts - timedelta(days=10)
        )
        if covered and not cached.empty:
            self._log(f"行情缓存命中 {sym.full_code} {adjust} · {len(cached)} 根")
            cached.attrs["warning"] = ""
            return cached
        if not self.offline:
            try:
                fetched = self._fetch_stock_network(sym, start_ts, end_ts, adjust)
                if not fetched.empty:
                    self.cache.write_bars(sym.full_code, adjust, fetched)
                    self._log(
                        f"AKShare 获取 {sym.full_code} {adjust} · {len(fetched)} 根 · "
                        f"{fetched.attrs.get('source', 'akshare')}"
                    )
                    fetched.attrs["cached"] = False
                    fetched.attrs["warning"] = ""
                    return fetched
            except Exception as exc:
                self._log(f"在线行情获取失败: {exc}")
                if not cached.empty:
                    cached.attrs["warning"] = f"在线获取失败，使用部分缓存: {exc}"
                    return cached
                if not allow_sample:
                    raise DataError(f"行情获取失败且无本地缓存: {exc}") from exc
        sample = self._load_sample(sym.full_code, adjust, start_ts, end_ts)
        if not sample.empty:
            self._log(f"使用内置离线示例数据 {sym.full_code} {adjust} · {len(sample)} 根")
            sample.attrs["warning"] = "当前使用内置离线示例数据，仅用于演示与测试"
            return sample
        if not cached.empty:
            cached.attrs["warning"] = "在线获取失败，使用本地部分缓存"
            return cached
        raise DataError("无可用行情数据：在线获取失败且没有本地缓存或示例数据")

    def load_index(
        self,
        symbol: str,
        start: str | date | datetime,
        end: str | date | datetime,
        allow_sample: bool = True,
    ) -> pd.DataFrame:
        code = parse_index(symbol)
        start_ts = _norm_date(start)
        end_ts = _norm_date(end)
        cached = self.cache.read_index(code, start_ts, end_ts)
        min_date, max_date = self.cache.index_coverage(code)
        covered = bool(
            min_date is not None
            and max_date is not None
            and min_date <= start_ts + timedelta(days=7)
            and max_date >= end_ts - timedelta(days=10)
        )
        if covered and not cached.empty:
            return cached
        if not self.offline:
            try:
                fetched = self._fetch_index_network(code)
                fetched = fetched[(fetched["date"] >= start_ts) & (fetched["date"] <= end_ts)].reset_index(drop=True)
                if not fetched.empty:
                    self.cache.write_index(code, fetched)
                    self._log(f"AKShare 获取基准 {code} · {len(fetched)} 根")
                    return fetched
            except Exception as exc:
                self._log(f"基准获取失败: {exc}")
        if not cached.empty:
            cached.attrs["warning"] = "基准使用本地缓存"
            return cached
        sample = self._load_sample_index(code, start_ts, end_ts)
        if not sample.empty:
            sample.attrs["warning"] = "基准使用内置离线示例数据"
            return sample
        if allow_sample:
            raise DataError("基准指数无可用数据")
        return pd.DataFrame()

    def _fetch_stock_network(self, sym: Symbol, start: pd.Timestamp, end: pd.Timestamp, adjust: str) -> pd.DataFrame:
        errors: list[str] = []
        start_s = start.strftime("%Y%m%d")
        end_s = end.strftime("%Y%m%d")
        try:
            import akshare as ak

            self._log(f"尝试东方财富行情 {sym.code} ...")
            df = ak.stock_zh_a_hist(
                symbol=sym.code,
                period="daily",
                start_date=start_s,
                end_date=end_s,
                adjust=adjust,
                timeout=12,
            )
            out = _standardize(df, "eastmoney", start, end)
            if not out.empty:
                return out
        except Exception as exc:
            errors.append(f"东方财富: {exc}")
        try:
            import akshare as ak

            self._log(f"尝试腾讯行情 {sym.full_code} ...")
            with contextlib.redirect_stderr(io.StringIO()):
                df = ak.stock_zh_a_hist_tx(
                    symbol=sym.full_code,
                    start_date=start_s,
                    end_date=end_s,
                    adjust=adjust,
                )
            out = _standardize(df, "tencent", start, end)
            if not out.empty:
                return out
        except Exception as exc:
            errors.append(f"腾讯: {exc}")
        try:
            import akshare as ak

            self._log(f"尝试新浪行情 {sym.full_code} ...")
            df = ak.stock_zh_a_daily(
                symbol=sym.full_code,
                start_date=start_s,
                end_date=end_s,
                adjust=adjust,
            )
            out = _standardize(df, "sina", start, end)
            if not out.empty:
                out["volume"] = out["volume"] / 100.0
                out.attrs["source"] = "sina"
                return out
        except Exception as exc:
            errors.append(f"新浪: {exc}")
        raise DataError("; ".join(errors))

    def _fetch_index_network(self, code: str) -> pd.DataFrame:
        errors: list[str] = []
        try:
            import akshare as ak

            df = ak.stock_zh_index_daily(symbol=code)
            out = _standardize(df, "sina-index", pd.Timestamp("1990-01-01"), pd.Timestamp("2100-01-01"))
            if not out.empty:
                return out
        except Exception as exc:
            errors.append(f"新浪指数: {exc}")
        try:
            import akshare as ak

            digits = code[2:]
            df = ak.index_zh_a_hist(symbol=digits, period="daily", start_date="19900101", end_date="20500101")
            out = _standardize(df, "eastmoney-index", pd.Timestamp("1990-01-01"), pd.Timestamp("2100-01-01"))
            if not out.empty:
                return out
        except Exception as exc:
            errors.append(f"东财指数: {exc}")
        raise DataError("; ".join(errors))

    def _load_sample(self, full_code: str, adjust: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        if not self.sample_dir:
            return pd.DataFrame()
        path = self.sample_dir / f"{full_code}_{adjust}.csv"
        if not path.exists():
            return pd.DataFrame()
        df = pd.read_csv(path)
        out = _standardize(df, "sample", start, end)
        if not out.empty:
            out.attrs["cached"] = True
            out.attrs["sample"] = True
        return out

    def _load_sample_index(self, code: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        if not self.sample_dir:
            return pd.DataFrame()
        path = self.sample_dir / f"{code}.csv"
        if not path.exists():
            return pd.DataFrame()
        df = pd.read_csv(path)
        out = _standardize(df, "sample", start, end)
        if not out.empty:
            out.attrs["cached"] = True
            out.attrs["sample"] = True
        return out
