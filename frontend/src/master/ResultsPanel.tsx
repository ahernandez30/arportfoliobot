import { formatPct } from '../market/bars'
import { formatMoney } from '../money'
import type { CreditSpreads, Luck, OpenCesta, Results, SpreadSide } from './types'

const TYPE_LABEL: Record<string, string> = { LLENA: 'LLENA', FLECO: 'FLECO', ENGULFING: 'ENGULF', RACHA: 'RACHA' }

function pct(v: number | null, digits = 0): string {
  return v == null ? '—' : `${v.toFixed(digits)}%`
}

function days(v: number | null): string {
  if (v == null) return '·'
  return v < 1 ? `${(v * 24).toFixed(1)} h` : `${v.toFixed(1)} d`
}

function rateClass(v: number | null): string {
  if (v == null) return 'flat'
  return v >= 55 ? 'gain' : v < 45 ? 'loss' : 'warn-text'
}

function signClass(v: number | null): string {
  return v == null ? 'flat' : v >= 0 ? 'gain' : 'loss'
}

/** The script's results tables: by candle type, totals, capital, longs and shorts, streaks,
 * duration, what is stopping signals now, and year by year. */
export function ResultsPanel({ r, ruleText, now, openNow }: {
  r: Results
  ruleText: string
  now?: string[]
  openNow?: { longs: number; shorts: number; cesta?: OpenCesta | null } | null
}) {
  const rows = [...r.by_type, { type: 'TOTAL', ...r.total, gap_avg: null, gap_wins: 0, gap_n: 0, floating_avg: null }]
  const cap = r.capital
  return (
    <div className="results">
      {now && (
        <p className={now.length ? 'msg msg-warn' : 'msg msg-ok'}>
          Now: {now.length ? now.join(' · ') : 'free, nothing is stopping new signals'}
          {openNow && r.mode === 'target_stop' && <> · open: <span className="num">{openNow.longs}</span> long, <span className="num">{openNow.shorts}</span> short</>}
          {openNow?.cesta && r.mode === 'target_stop' && <> · CESTA {openNow.cesta.pct === null ? 'nothing open' : <span className="num">{formatPct(openNow.cesta.pct)}</span>} of <span className="num">{openNow.cesta.target}%</span>, <span className="num">{openNow.cesta.closed}</span> closed</>}
        </p>
      )}
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>Type</th><th className="right">Win rate</th><th className="right">W-L</th><th className="right">Floating</th><th className="right">Gap</th><th className="right">Avg R</th><th className="right">Days win / loss</th></tr>
          </thead>
          <tbody>
            {rows.map((t) => {
              const tot = t.wins + t.losses
              return (
                <tr key={t.type} className={t.type === 'TOTAL' ? 'total-row' : ''}>
                  <td>{TYPE_LABEL[t.type] ?? t.type}</td>
                  <td className={`num right ${tot ? rateClass(t.win_rate) : 'flat'}`}>{tot ? pct(t.win_rate) : '—'}</td>
                  <td className="num right">{t.wins}-{t.losses}</td>
                  <td className="num right">{t.floating}{t.floating_avg != null ? ` ${formatPct(t.floating_avg)}` : ''}</td>
                  <td className="num right">{t.gap_avg != null ? `${formatPct(t.gap_avg)} (${t.gap_wins}g)` : '—'}</td>
                  <td className={`num right ${signClass(t.r_avg)}`}>{formatPct(t.r_avg)}</td>
                  <td className="num right">{t.days_win != null || t.days_loss != null ? `${t.days_win?.toFixed(1) ?? '·'} / ${t.days_loss?.toFixed(1) ?? '·'}` : '—'}</td>
                </tr>
              )
            })}
            {(r.by_side ?? []).map((s) => {
              const tot = s.wins + s.losses
              return (
                <tr key={s.side}>
                  <td>{s.side === 'long' ? 'LONGS ▲' : 'SHORTS ▼'}</td>
                  <td className={`num right ${tot ? rateClass(s.win_rate) : 'flat'}`}>{tot ? pct(s.win_rate) : '—'}</td>
                  <td className="num right">{s.wins}-{s.losses}</td>
                  <td /><td />
                  <td className={`num right ${signClass(s.r_avg)}`}>{formatPct(s.r_avg)}</td>
                  <td />
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {cap && <p className="muted">
        <b>Capital (stock):</b> <span className={`num ${signClass(cap.gain)}`}>{formatMoney(cap.final)}</span> from {formatMoney(cap.start)}
        {' '}(<span className={`num ${signClass(cap.gain)}`}>{formatPct(cap.return_pct)}</span>) · {cap.pct_per_trade}% per trade, {cap.compound ? 'compounding' : 'simple (on the starting capital)'}
        {' '}· <span className="num">{cap.trades}</span> trades · biggest drop <span className="num loss">−{cap.max_drop_pct.toFixed(1)}%</span>
      </p>}
      <p className="muted">
        {ruleText} · Candles measured <span className="num">{r.candles_measured.toLocaleString()}</span> · Longest winning streak{' '}
        <span className="num gain">{r.max_win_streak}</span> · losing streak <span className="num loss">{r.max_loss_streak}</span>
        {r.hits && <> · Hits in a row (all that closes on one candle counts once): <span className="num gain">{r.hits.max_won_in_a_row}</span> won,{' '}
          <span className="num loss">{r.hits.max_lost_in_a_row}</span> lost · worst run <span className="num loss">{formatMoney(-r.hits.worst_run)}</span>{' '}
          ({r.hits.worst_run_pct.toFixed(1)}% of the account)</>} · Average trade{' '}
        <span className="num">{days(r.avg_days)}</span>{r.avg_bars != null && <> (<span className="num">{r.avg_bars.toFixed(1)}</span> candles)</>}
        {r.mode === 'target_stop' && <> · Decided by the real path: <span className="num">{r.real_path_trades}</span></>}
      </p>
      {r.years.length > 0 && (
        <details>
          <summary>Year by year</summary>
          <div className="table-wrap years">
            <table className="table">
              <thead><tr><th>Year</th><th className="right">Won</th><th className="right">Lost</th><th className="right">%</th><th className="right">$</th><th className="right">% of account</th></tr></thead>
              <tbody>
                {[...r.years].reverse().map((y) => {
                  const n = y.wins + y.losses
                  const p = n ? (100 * y.wins) / n : null
                  return (
                    <tr key={y.year}>
                      <td className="num">{y.year}</td>
                      <td className="num right gain">{y.wins}</td>
                      <td className="num right loss">{y.losses}</td>
                      <td className={`num right ${rateClass(p)}`}>{pct(p)}</td>
                      <td className={`num right ${signClass(y.pnl ?? null)}`}>{formatMoney(y.pnl, true)}</td>
                      <td className={`num right ${signClass(y.pnl ?? null)}`}>{formatPct(y.pnl_pct)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <p className="muted">Won and lost count by the year a trade opened; dollars by the year it closed, as in the script.</p>
        </details>
      )}
    </div>
  )
}

const VERDICT: Record<string, string> = { yes: '✔ yes', weak: '~ weak', no: '✗ no', '-': '—' }

/** The script's luck test ("prueba de azar"): is the signal skill or luck? */
export function LuckPanel({ luck }: { luck: Luck }) {
  return (
    <div className="results">
      <p className="muted">
        Every signal re-run in shadow with the same fixed rule (target {luck.rule.objPct}%, stop {luck.rule.stopPct}%, {luck.rule.maxVelas} candles): in its own
        direction, forced long and forced short; plus a long and a short on every candle.
        {luck.rule.fixed && ' Your real trades also exit by other rules (signal to signal, opposite signal or the weekly change); the luck test still uses this fixed rule.'}
      </p>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Compared with</th><th className="right">Win rate</th><th className="right">Avg R</th><th className="right">n</th></tr></thead>
          <tbody>
            {luck.rows.map((r, i) => (
              <tr key={r.label} className={i === 0 ? 'total-row' : ''}>
                <td>{r.label}</td>
                <td className="num right">{pct(r.win_rate)}</td>
                <td className={`num right ${signClass(r.r_avg)}`}>{formatPct(r.r_avg)}</td>
                <td className="num right">{r.n}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Question</th><th className="right">t</th><th className="right">Chance it is luck</th><th>Verdict</th></tr></thead>
          <tbody>
            {luck.verdicts.map((v) => (
              <tr key={v.label}>
                <td>{v.label}</td>
                <td className="num right">{v.t == null ? '—' : v.t.toFixed(2)}</td>
                <td className="num right">{v.luck_pct == null ? '—' : v.luck_pct < 0.1 ? '<0.1%' : `${v.luck_pct.toFixed(1)}%`}</td>
                <td className={v.verdict === 'yes' ? 'gain' : v.verdict === 'no' ? 'loss' : 'warn-text'}>{VERDICT[v.verdict] ?? v.verdict.replace('few', 'too few')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function payout(v: number | null): string {
  return v == null ? '—' : v > 20 ? '>20:1' : `${v.toFixed(2)}:1`
}

function rr(v: number | null): string {
  return v == null ? '—' : `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(2)}R`
}

function sideCell(s: SpreadSide) {
  const cls = s.r == null ? 'flat' : s.n < 30 ? 'warn-text' : s.r > 0 ? 'gain' : 'loss'
  return <td className={`num right ${cls}`}>needs {payout(s.needed)} · random {payout(s.random_payout)} · {rr(s.r)}</td>
}

/** The script's credit-spread statistics (v9.33, v9.34): where the price closed at expiration after
 * each signal, for several strikes and expirations, and a dollar simulation of one of them. */
export function CreditSpreadsPanel({ cs }: { cs: CreditSpreads }) {
  const sim = cs.simulation
  return (
    <div className="results">
      <p className="muted">
        A credit spread opened at every signal and held to expiration: a buy sells a put spread with its short strike that far <b>above</b> the price,
        a sell a call spread with its short strike that far <b>below</b>; width {cs.width_pct}% of the price. “Needs” is the smallest payout the real
        option chain must offer for the signal not to lose money; “random” is what the same spread paid on average when opened on any candle; R is per $1
        risked at the random payout. The row closest to a {cs.target_payout}:1 payout is highlighted. Yellow: fewer than 30 signals.
      </p>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>Days</th><th className="right">Strike</th><th className="right">n</th><th className="right">Win all · signal / random</th><th className="right">Lose all</th><th className="right">Longs ▲ (put spread)</th><th className="right">Shorts ▼ (call spread)</th><th className="right">Total</th></tr>
          </thead>
          <tbody>
            {cs.blocks.map((b) => b.rows.map((row, j) => (
              <tr key={`${b.days}-${j}`} className={row.closest ? 'current-row' : ''}>
                <td className="num">{j === 0 ? `${b.days} d` : ''}</td>
                <td className="num right">{row.strike_pct}%</td>
                <td className="num right">{row.long.n} ▲ · {row.short.n} ▼</td>
                <td className={`num right ${row.win_all == null || row.win_all_random == null ? 'flat' : row.win_all > row.win_all_random ? 'gain' : 'loss'}`}>
                  {pct(row.win_all)} / {pct(row.win_all_random)}
                </td>
                <td className="num right">{pct(row.lose_all)}</td>
                {sideCell(row.long)}
                {sideCell(row.short)}
                <td className={`num right ${signClass(row.total_r)}`}>{rr(row.total_r)}</td>
              </tr>
            )))}
          </tbody>
        </table>
      </div>
      <p className={sim.broke ? 'msg msg-error' : 'muted'}>
        <b>Simulation</b> · {sim.days} days · strike {sim.strike_pct}% · payout {sim.payout}:1 · {formatMoney(sim.risk_per_spread)} per spread · account {formatMoney(sim.account)}
        {' '}→ <span className={`num ${signClass(sim.total)}`}>{formatMoney(sim.total, true)}</span> ({formatPct(sim.total_pct)}) · <span className="num">{sim.spreads}</span> spreads ·
        win {pct(sim.win_rate)} · lose everything {pct(sim.lose_all_rate)} · biggest drop {formatMoney(-sim.max_drop)} ({sim.max_drop_pct.toFixed(0)}% of the account) ·
        worst run {formatMoney(-sim.worst_run)} · most open at once <span className="num">{sim.max_open}</span> ({formatMoney(sim.max_at_risk)} at risk)
        {sim.broke && <> · the account went broke: it reached {formatMoney(sim.lowest_account)}</>}
      </p>
      {sim.years.length > 0 && (
        <p className="muted">By year (of expiration): {sim.years.map((y) => `${y.year} ${formatMoney(y.pnl, true)}`).join(' · ')}</p>
      )}
    </div>
  )
}
