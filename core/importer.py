from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

from core.datafeed import CacheStore, DataError, parse_symbol


SUPPORTED_EXTENSIONS = {".csv", ".txt", ".db", ".sqlite", ".sqlite3"}
SYMBOL_ALIASES = ("symbol", "code", "ticker", "股票代码", "证券代码", "代码", "合约代码")
DATE_ALIASES = ("date", "datetime", "trade_date", "trade_datetime", "日期", "交易日期", "时间")
OPEN_ALIASES = ("open", "开盘", "开盘价")
HIGH_ALIASES = ("high", "最高", "最高价")
LOW_ALIASES = ("low", "最低", "最低价")
CLOSE_ALIASES = ("close", "收盘", "收盘价")
VOLUME_ALIASES = ("volume", "vol", "成交量", "成交量(手)", "成交量（手）", "量")
AMOUNT_ALIASES = ("amount", "turnover", "成交额", "成交金额", "成交额(元)", "成交额（元）")
CODE_RE = re.compile(r"(?i)(sh|sz|bj)?(\d{6})")


@dataclass
class ImportReport:
    files: int = 0
    rows: int = 0
    symbols: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    duration: float = 0.0
    adjusts: dict[str, int] = field(default_factory=dict)


def collect_paths(paths: Iterable[str | Path]) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        path = Path(raw).expanduser()
        if not path.exists():
            continue
        candidates = [path] if path.is_file() else [item for item in path.rglob("*") if item.is_file()]
        for item in candidates:
            if item.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            if item.name.startswith(".") or item.name.lower().startswith("readme"):
                continue
            resolved = item.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(item)
    return sorted(found, key=lambda item: str(item).lower())


def infer_adjust(path: Path, default_adjust: str = "auto", is_database: bool = False) -> str:
    lowered = path.name.lower()
    if "hfq" in lowered or "后复权" in lowered:
        return "hfq"
    if "qfq" in lowered or "前复权" in lowered:
        return "qfq"
    if "raw" in lowered or "不复权" in lowered:
        return ""
    if default_adjust in ("qfq", "hfq", ""):
        return default_adjust
    return "hfq" if is_database else "qfq"


def _find_column(columns: list[str], aliases: tuple[str, ...]) -> str | None:
    lowered = {str(col).strip().lower(): col for col in columns}
    for alias in aliases:
        if alias.lower() in lowered:
            return lowered[alias.lower()]
    return None


def _read_csv(path: Path) -> pd.DataFrame:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return pd.read_csv(path, encoding=encoding, low_memory=False)
        except UnicodeDecodeError as exc:
            last_error = exc
        except Exception as exc:
            last_error = exc
            break
    raise DataError(f"CSV 读取失败: {last_error}")


def _infer_symbol_from_path(path: Path) -> str | None:
    match = CODE_RE.search(path.stem.lower())
    if not match:
        return None
    prefix, digits = match.groups()
    try:
        return parse_symbol(f"{prefix or ''}{digits}").full_code
    except DataError:
        return None


def _normalize_csv(path: Path, frame: pd.DataFrame, default_adjust: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    frame = frame.copy()
    frame.columns = [str(col).strip() for col in frame.columns]
    columns = list(frame.columns)
    date_col = _find_column(columns, DATE_ALIASES)
    open_col = _find_column(columns, OPEN_ALIASES)
    high_col = _find_column(columns, HIGH_ALIASES)
    low_col = _find_column(columns, LOW_ALIASES)
    close_col = _find_column(columns, CLOSE_ALIASES)
    if not all((date_col, open_col, high_col, low_col, close_col)):
        raise DataError(f"缺少日期或 OHLC 列: {columns}")
    volume_col = _find_column(columns, VOLUME_ALIASES)
    amount_col = _find_column(columns, AMOUNT_ALIASES)
    symbol_col = _find_column(columns, SYMBOL_ALIASES)
    raw_dates = frame[date_col].astype(str).str.strip()
    parsed_dates = pd.to_datetime(raw_dates, errors="coerce")
    eight = raw_dates.str.fullmatch(r"\d{8}")
    if eight.any():
        parsed_dates.loc[eight] = pd.to_datetime(raw_dates[eight], format="%Y%m%d", errors="coerce")
    work = pd.DataFrame(
        {
            "date": parsed_dates.dt.normalize(),
            "open": pd.to_numeric(frame[open_col], errors="coerce"),
            "high": pd.to_numeric(frame[high_col], errors="coerce"),
            "low": pd.to_numeric(frame[low_col], errors="coerce"),
            "close": pd.to_numeric(frame[close_col], errors="coerce"),
            "volume": pd.to_numeric(frame[volume_col], errors="coerce").fillna(0.0) if volume_col else 0.0,
            "amount": pd.to_numeric(frame[amount_col], errors="coerce").fillna(0.0) if amount_col else 0.0,
        }
    )
    if symbol_col:
        raw_symbols = frame[symbol_col].astype(str).str.strip()
    else:
        symbol = _infer_symbol_from_path(path)
        if symbol is None:
            raise DataError("无法从文件名识别股票代码，且文件没有代码列")
        raw_symbols = pd.Series(symbol, index=frame.index)
    symbols: list[str] = []
    fallback = _infer_symbol_from_path(path)
    for raw in raw_symbols:
        value = str(raw).strip()
        try:
            symbol = parse_symbol(value).full_code
        except DataError:
            if fallback is None:
                raise DataError(f"股票代码无效: {value}")
            symbol = fallback
        symbols.append(symbol)
    work["symbol"] = symbols
    work["adjust"] = infer_adjust(path, default_adjust, is_database=False)
    work["source"] = f"import:{path.name}"
    work = work.dropna(subset=["date", "open", "high", "low", "close"])
    work = work[(work["close"] > 0) & (work["open"] > 0)]
    work = work.drop_duplicates(["symbol", "date"], keep="last").sort_values(["symbol", "date"])
    return work.reset_index(drop=True)


def _import_csv(path: Path, cache: CacheStore, default_adjust: str, progress) -> tuple[int, int, dict[str, int]]:
    frame = _read_csv(path)
    work = _normalize_csv(path, frame, default_adjust)
    if work.empty:
        return 0, 0, {}
    progress(f"导入 CSV {path.name}: {len(work):,} 行，{work['symbol'].nunique()} 只股票，复权={work['adjust'].iloc[0] or 'raw'}")
    rows = cache.write_bulk(work)
    counts = work.groupby("adjust").size().to_dict()
    return rows, int(work["symbol"].nunique()), {str(key or "raw"): int(value) for key, value in counts.items()}


def _sqlite_tables(con: sqlite3.Connection) -> list[str]:
    return [str(row[0]) for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]


def _sqlite_columns(con: sqlite3.Connection, table: str) -> dict[str, str]:
    return {str(row[1]).lower(): str(row[1]) for row in con.execute(f'PRAGMA table_info("{table}")')}


def _import_sqlite(path: Path, cache_path: Path, default_adjust: str, progress) -> tuple[int, int, dict[str, int]]:
    if path.resolve() == cache_path.resolve():
        raise DataError("不能导入当前正在使用的缓存数据库")
    try:
        source = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=30)
    except sqlite3.Error as exc:
        raise DataError(f"数据库打开失败: {exc}") from exc
    try:
        tables = _sqlite_tables(source)
        chosen = None
        for name in ("bars", "daily_bars", "stock_daily", "stock_daily_bars", "daily"):
            if name in tables:
                chosen = name
                break
        if chosen is None:
            raise DataError(f"未找到可识别的行情表: {tables}")
        cols = _sqlite_columns(source, chosen)
        progress(f"发现数据库表 {chosen}: {', '.join(cols.values())}")
        has_symbol = any(key in cols for key in ("symbol", "code", "ticker"))
        has_date = "date" in cols or "trade_date" in cols or "日期" in cols
        if not (has_symbol and has_date and all(key in cols for key in ("open", "high", "low", "close"))):
            raise DataError("数据库表字段不兼容：需要 symbol/date/open/high/low/close")
        source.close()
        source = None
        target = sqlite3.connect(cache_path, timeout=60)
        target.execute("PRAGMA journal_mode=WAL")
        target.execute("PRAGMA synchronous=NORMAL")
        target.execute("ATTACH DATABASE ? AS src", (str(path),))
        try:
            if chosen == "bars" and "adjust" in cols:
                amount_expr = "COALESCE(amount, 0)" if "amount" in cols else "0"
                select = (
                    "SELECT symbol, adjust, date, open, high, low, close, "
                    f"COALESCE(volume, 0), {amount_expr}, 'import:db', datetime('now') FROM src.bars WHERE 1"
                )
                target.execute(
                    "INSERT INTO bars(symbol, adjust, date, open, high, low, close, volume, amount, source, updated_at) "
                    f"{select} ON CONFLICT(symbol, adjust, date) DO UPDATE SET "
                    "open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close, "
                    "volume=excluded.volume, amount=excluded.amount, source=excluded.source, updated_at=excluded.updated_at"
                )
                target.commit()
                rows = target.execute("SELECT COUNT(*) FROM src.bars").fetchone()[0]
                symbols = target.execute("SELECT COUNT(DISTINCT symbol) FROM src.bars").fetchone()[0]
                adjust_rows = target.execute("SELECT adjust, COUNT(*) FROM src.bars GROUP BY adjust").fetchall()
                return int(rows), int(symbols), {str(key or "raw"): int(value) for key, value in adjust_rows}
            adjust = infer_adjust(path, default_adjust, is_database=True)
            code_expr = "code" if "code" in cols else ("symbol" if "symbol" in cols else "ticker")
            date_expr = "date" if "date" in cols else ("trade_date" if "trade_date" in cols else "日期")
            normalized_date = (
                f"CASE WHEN length({date_expr})=8 AND instr({date_expr}, '-')=0 "
                f"THEN substr({date_expr},1,4)||'-'||substr({date_expr},5,2)||'-'||substr({date_expr},7,2) "
                f"ELSE substr({date_expr},1,10) END"
            )
            if "amount" in cols:
                amount_expr = "COALESCE(amount, 0)"
            elif "turnover" in cols:
                amount_expr = "COALESCE(turnover, 0)"
            else:
                amount_expr = "0"
            volume_expr = "transaction_volume" if "transaction_volume" in cols else ("volume" if "volume" in cols else ("vol" if "vol" in cols else "0"))
            symbol_expr = (
                f"CASE "
                f"WHEN lower({code_expr}) GLOB 'sh[0-9]*' OR lower({code_expr}) GLOB 'sz[0-9]*' "
                f"OR lower({code_expr}) GLOB 'bj[0-9]*' THEN lower({code_expr}) "
                f"WHEN {code_expr} GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]' THEN "
                f"CASE WHEN {code_expr} GLOB '6*' THEN 'sh'||{code_expr} "
                f"WHEN {code_expr} GLOB '0*' OR {code_expr} GLOB '3*' THEN 'sz'||{code_expr} "
                f"ELSE 'bj'||{code_expr} END "
                f"ELSE lower({code_expr}) END"
            )
            select = (
                f"SELECT {symbol_expr}, '{adjust}', {normalized_date}, open, high, low, close, "
                f"COALESCE({volume_expr}, 0), {amount_expr}, 'import:db', datetime('now') FROM src.\"{chosen}\" WHERE 1"
            )
            target.execute(
                "INSERT INTO bars(symbol, adjust, date, open, high, low, close, volume, amount, source, updated_at) "
                f"{select} ON CONFLICT(symbol, adjust, date) DO UPDATE SET "
                "open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close, "
                "volume=excluded.volume, amount=excluded.amount, source=excluded.source, updated_at=excluded.updated_at"
            )
            target.commit()
            rows = target.execute(f'SELECT COUNT(*) FROM src."{chosen}"').fetchone()[0]
            symbols = target.execute(f'SELECT COUNT(DISTINCT {symbol_expr}) FROM src."{chosen}"').fetchone()[0]
            progress(f"数据库导入完成: {rows:,} 行，{symbols:,} 只，复权={adjust or 'raw'}")
            return int(rows), int(symbols), {adjust or "raw": int(rows)}
        finally:
            try:
                target.execute("DETACH DATABASE src")
            except sqlite3.Error:
                pass
            target.close()
    finally:
        if source is not None:
            source.close()


def import_paths(
    paths: Iterable[str | Path],
    cache_path: str | Path,
    default_adjust: str = "auto",
    progress: Callable[[str], None] | None = None,
) -> ImportReport:
    progress = progress or (lambda _message: None)
    cache = CacheStore(cache_path)
    files = collect_paths(paths)
    report = ImportReport(files=0)
    start_time = time.time()
    symbols: set[str] = set()
    for path in files:
        report.files += 1
        try:
            suffix = path.suffix.lower()
            if suffix in (".db", ".sqlite", ".sqlite3"):
                rows, count, adjusts = _import_sqlite(path, cache.path, default_adjust, progress)
            else:
                rows, count, adjusts = _import_csv(path, cache, default_adjust, progress)
            if rows == 0:
                report.skipped += 1
                report.errors.append(f"{path.name}: 没有有效数据")
                continue
            report.rows += rows
            for key, value in adjusts.items():
                report.adjusts[key] = report.adjusts.get(key, 0) + int(value)
            progress(f"完成 {path.name}: {rows:,} 行")
        except Exception as exc:
            report.skipped += 1
            report.errors.append(f"{path.name}: {exc}")
            progress(f"跳过 {path.name}: {exc}")
    with cache.connect() as con:
        report.symbols = int(con.execute("SELECT COUNT(DISTINCT symbol) FROM bars").fetchone()[0])
    report.duration = time.time() - start_time
    return report
