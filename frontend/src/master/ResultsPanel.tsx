import { formatPct } from '../market/bars'
import type { Luck, Results } from './types'

const TYPE_LABEL: Record<string, string> = { LLENA: 'LLENA', FLECO: 'FLECO', ENGULFING: 'ENGULF' }

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

/** The script's results tables: by candle type, totals, streaks, duration, and year by year. */
export function ResultsPanel({ r, ruleText }: { r: Results; ruleText: string }) {
  const rows = [...r.by_type, { type: 'TOTAL', ...r.total, gap_avg: null, gap_wins: 0, gap_n: 0, floating_avg: null }]
  return (
    <div className="results">
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
                  <td className={`num right ${t.r_avg == null ? 'flat' : t.r_avg >= 0 ? 'gain' : 'loss'}`}>{formatPct(t.r_avg)}</td>
                  <td className="num right">{t.days_win != null || t.days_loss != null ? `${t.days_win?.toFixed(1) ?? '·'} / ${t.days_loss?.toFixed(1) ?? '·'}` : '—'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="muted">
        {ruleText} · Candles measured <span className="num">{r.candles_measured.toLocaleString()}</span> · Longest winning streak{' '}
        <span className="num gain">{r.max_win_streak}</span> · losing streak <span className="num loss">{r.max_loss_streak}</span> · Average trade{' '}
        <span className="num">{days(r.avg_days)}</span>{r.avg_bars != null && <> (<span className="num">{r.avg_bars.toFixed(1)}</span> candles)</>}
        {r.mode === 'target_stop' && <> · Decided by the real path: <span className="num">{r.real_path_trades}</span></>}
      </p>
      {r.years.length > 0 && (
        <details>
          <summary>Year by year</summary>
          <div className="table-wrap years">
            <table className="table">
              <thead><tr><th>Year</th><th className="right">Won</th><th className="right">Lost</th><th className="right">%</th></tr></thead>
              <tbody>
                {[...r.years].reverse().map((y) => {
                  const p = (100 * y.wins) / (y.wins + y.losses)
                  return (
                    <tr key={y.year}>
                      <td className="num">{y.year}</td>
                      <td className="num right gain">{y.wins}</td>
                      <td className="num right loss">{y.losses}</td>
                      <td className={`num right ${rateClass(p)}`}>{p.toFixed(0)}%</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
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
      </p>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Compared with</th><th className="right">Win rate</th><th className="right">Avg R</th><th className="right">n</th></tr></thead>
          <tbody>
            {luck.rows.map((r, i) => (
              <tr key={r.label} className={i === 0 ? 'total-row' : ''}>
                <td>{r.label}</td>
                <td className="num right">{pct(r.win_rate)}</td>
                <td className={`num right ${r.r_avg == null ? 'flat' : r.r_avg >= 0 ? 'gain' : 'loss'}`}>{formatPct(r.r_avg)}</td>
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
