use std::os::raw::{c_int, c_longlong};
use std::panic::{catch_unwind, AssertUnwindSafe};

#[repr(C)]
pub struct BtConfig {
    pub initial_cash: f64,
    pub commission_rate: f64,
    pub min_commission: f64,
    pub stamp_tax_rate: f64,
    pub transfer_fee_rate: f64,
    pub slippage_bps: f64,
    pub lot_size: c_int,
    pub position_pct: f64,
    pub stop_loss_pct: f64,
    pub take_profit_pct: f64,
    pub max_hold_days: c_int,
    pub close_at_end: c_int,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct BtTrade {
    pub entry_idx: c_int,
    pub exit_idx: c_int,
    pub entry_signal_idx: c_int,
    pub exit_signal_idx: c_int,
    pub exit_code: c_int,
    pub _pad: c_int,
    pub shares: c_longlong,
    pub entry_price: f64,
    pub exit_price: f64,
    pub entry_notional: f64,
    pub exit_notional: f64,
    pub entry_fee: f64,
    pub exit_fee: f64,
    pub pnl: f64,
    pub return_pct: f64,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct BtOpen {
    pub shares: c_longlong,
    pub entry_idx: c_int,
    pub _pad: c_int,
    pub entry_price: f64,
    pub entry_notional: f64,
    pub entry_fee: f64,
}

#[inline]
fn buy_fee(notional: f64, cfg: &BtConfig) -> f64 {
    notional * cfg.commission_rate.max(cfg.min_commission / notional.max(1e-12))
        + notional * cfg.transfer_fee_rate
}

#[inline]
fn sell_fee(notional: f64, cfg: &BtConfig) -> f64 {
    notional * cfg.commission_rate.max(cfg.min_commission / notional.max(1e-12))
        + notional * cfg.transfer_fee_rate
        + notional * cfg.stamp_tax_rate
}

unsafe fn run_impl(
    n: c_int,
    open: *const f64,
    close: *const f64,
    entry: *const u8,
    exit_sig: *const u8,
    cfg_ptr: *const BtConfig,
    equity: *mut f64,
    cash_out: *mut f64,
    posval_out: *mut f64,
    exposure_out: *mut f64,
    trades_out: *mut BtTrade,
    max_trades: c_int,
    open_out: *mut BtOpen,
) -> c_int {
    if n <= 0 || open.is_null() || close.is_null() || entry.is_null() || exit_sig.is_null()
        || cfg_ptr.is_null() || equity.is_null() || cash_out.is_null() || posval_out.is_null()
        || exposure_out.is_null() || trades_out.is_null() || open_out.is_null()
    {
        return -1;
    }
    let cfg = &*cfg_ptr;
    if cfg.initial_cash <= 0.0 || cfg.lot_size <= 0 || cfg.position_pct <= 0.0
        || cfg.commission_rate < 0.0 || cfg.min_commission < 0.0 || cfg.stamp_tax_rate < 0.0
        || cfg.transfer_fee_rate < 0.0 || cfg.slippage_bps < 0.0
    {
        return -1;
    }
    let n = n as usize;
    let open = std::slice::from_raw_parts(open, n);
    let close = std::slice::from_raw_parts(close, n);
    let entry = std::slice::from_raw_parts(entry, n);
    let exit_sig = std::slice::from_raw_parts(exit_sig, n);
    let equity = std::slice::from_raw_parts_mut(equity, n);
    let cash_out = std::slice::from_raw_parts_mut(cash_out, n);
    let posval_out = std::slice::from_raw_parts_mut(posval_out, n);
    let exposure_out = std::slice::from_raw_parts_mut(exposure_out, n);
    let trades_out = std::slice::from_raw_parts_mut(trades_out, max_trades.max(0) as usize);
    let open_out = &mut *open_out;
    *open_out = BtOpen::default();

    let slip = cfg.slippage_bps / 10000.0;
    let lot = cfg.lot_size as i64;
    let mut cash = cfg.initial_cash;
    let mut shares: i64 = 0;
    let mut entry_price = 0.0f64;
    let mut entry_notional = 0.0f64;
    let mut entry_fee_paid = 0.0f64;
    let mut entry_idx: i64 = -1;
    let mut pending_entry = false;
    let mut pending_exit = false;
    let mut pending_exit_code = 0i32;
    let mut pending_exit_signal_idx = -1i32;
    let mut trade_count = 0usize;

    for i in 0..n {
        let i_i64 = i as i64;
        if i > 0 && (pending_exit || pending_entry) {
            if pending_exit && shares > 0 && i_i64 > entry_idx {
                let price = open[i] * (1.0 - slip);
                let notional = shares as f64 * price;
                let fee = sell_fee(notional, cfg);
                let proceeds = notional - fee;
                cash += proceeds;
                let pnl = proceeds - (entry_notional + entry_fee_paid);
                if trade_count < trades_out.len() {
                    trades_out[trade_count] = BtTrade {
                        entry_idx: entry_idx as c_int,
                        exit_idx: i as c_int,
                        entry_signal_idx: (entry_idx - 1) as c_int,
                        exit_signal_idx: pending_exit_signal_idx,
                        exit_code: pending_exit_code,
                        shares,
                        entry_price,
                        exit_price: price,
                        entry_notional,
                        exit_notional: notional,
                        entry_fee: entry_fee_paid,
                        exit_fee: fee,
                        pnl,
                        return_pct: if entry_notional + entry_fee_paid > 0.0 {
                            pnl / (entry_notional + entry_fee_paid) * 100.0
                        } else { 0.0 },
                        ..Default::default()
                    };
                    trade_count += 1;
                } else {
                    return -2;
                }
                shares = 0;
                entry_price = 0.0;
                entry_notional = 0.0;
                entry_fee_paid = 0.0;
                entry_idx = -1;
            }
            if pending_entry && shares == 0 {
                let price = open[i] * (1.0 + slip);
                let budget = cash * cfg.position_pct;
                let mut lots = if price > 0.0 { (budget / (price * lot as f64)).floor() as i64 } else { 0 };
                while lots > 0 {
                    let candidate_shares = lots * lot;
                    let candidate_notional = candidate_shares as f64 * price;
                    let candidate_fee = buy_fee(candidate_notional, cfg);
                    if candidate_notional + candidate_fee <= cash {
                        cash -= candidate_notional + candidate_fee;
                        shares = candidate_shares;
                        entry_price = price;
                        entry_idx = i_i64;
                        entry_notional = candidate_notional;
                        entry_fee_paid = candidate_fee;
                        break;
                    }
                    lots -= 1;
                }
            }
            pending_entry = false;
            pending_exit = false;
            pending_exit_code = 0;
            pending_exit_signal_idx = -1;
        }

        let position_value = shares as f64 * close[i];
        let total_equity = cash + position_value;
        equity[i] = total_equity;
        cash_out[i] = cash;
        posval_out[i] = position_value;
        exposure_out[i] = if total_equity > 0.0 { position_value / total_equity } else { 0.0 };

        if shares > 0 {
            let mut code = -1i32;
            if cfg.stop_loss_pct > 0.0 && close[i] <= entry_price * (1.0 - cfg.stop_loss_pct / 100.0) {
                code = 1;
            } else if cfg.take_profit_pct > 0.0 && close[i] >= entry_price * (1.0 + cfg.take_profit_pct / 100.0) {
                code = 2;
            } else if cfg.max_hold_days > 0 && (i_i64 - entry_idx) >= cfg.max_hold_days as i64 {
                code = 3;
            } else if exit_sig[i] != 0 {
                code = 0;
            }
            if code >= 0 && i + 1 < n {
                pending_exit = true;
                pending_exit_code = code;
                pending_exit_signal_idx = i as c_int;
            }
        } else if entry[i] != 0 && i + 1 < n {
            pending_entry = true;
        }
    }

    if shares > 0 && cfg.close_at_end != 0 && (entry_idx as usize) < n - 1 {
        let price = close[n - 1] * (1.0 - slip);
        let notional = shares as f64 * price;
        let fee = sell_fee(notional, cfg);
        let proceeds = notional - fee;
        cash += proceeds;
        let pnl = proceeds - (entry_notional + entry_fee_paid);
        if trade_count < trades_out.len() {
            trades_out[trade_count] = BtTrade {
                entry_idx: entry_idx as c_int,
                exit_idx: (n - 1) as c_int,
                entry_signal_idx: (entry_idx - 1) as c_int,
                exit_signal_idx: (n - 1) as c_int,
                exit_code: 4,
                shares,
                entry_price,
                exit_price: price,
                entry_notional,
                exit_notional: notional,
                entry_fee: entry_fee_paid,
                exit_fee: fee,
                pnl,
                return_pct: if entry_notional + entry_fee_paid > 0.0 {
                    pnl / (entry_notional + entry_fee_paid) * 100.0
                } else { 0.0 },
                ..Default::default()
            };
            trade_count += 1;
        } else {
            return -2;
        }
        equity[n - 1] = cash;
        cash_out[n - 1] = cash;
        posval_out[n - 1] = 0.0;
        exposure_out[n - 1] = 0.0;
        shares = 0;
    }

    if shares > 0 {
        *open_out = BtOpen {
            shares,
            entry_idx: entry_idx as c_int,
            entry_price,
            entry_notional,
            entry_fee: entry_fee_paid,
            ..Default::default()
        };
    }
    trade_count as c_int
}

#[no_mangle]
pub unsafe extern "C" fn bt_run(
    n: c_int,
    open: *const f64,
    close: *const f64,
    entry: *const u8,
    exit_sig: *const u8,
    cfg: *const BtConfig,
    equity: *mut f64,
    cash: *mut f64,
    posval: *mut f64,
    exposure: *mut f64,
    trades: *mut BtTrade,
    max_trades: c_int,
    open_out: *mut BtOpen,
) -> c_int {
    match catch_unwind(AssertUnwindSafe(|| run_impl(
        n, open, close, entry, exit_sig, cfg, equity, cash, posval, exposure, trades, max_trades, open_out,
    ))) {
        Ok(value) => value,
        Err(_) => -3,
    }
}
