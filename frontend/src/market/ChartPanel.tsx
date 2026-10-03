import { useState, type FormEvent } from 'react'
import { SYMBOL_RE } from '../forms'
import { TIMEFRAMES, type Timeframe } from './bars'
import Chart from './Chart'
import LivePrice from './LivePrice'

const QUICK = ['TSLA', 'QQQ', 'SPY']

/** A chart with its own symbol box and timeframe buttons. */
function SymbolBox({ symbol, onSymbol }: { symbol: string; onSymbol: (s: string) => void }) {
  const [text, setText] = useState(symbol)
  const [bad, setBad] = useState(false)

  function submit(e: FormEvent) {
    e.preventDefault()
    const s = text.trim().toUpperCase()
    if (!SYMBOL_RE.test(s)) return setBad(true)
    setBad(false)
    onSymbol(s)
  }

  return (
    <form onSubmit={submit} className="symbol-form">
      <input
        className={`input num symbol-input ${bad ? 'input-bad' : ''}`}
        aria-label="Symbol"
        value={text}
        maxLength={10}
        spellCheck={false}
        autoCapitalize="characters"
        onChange={(e) => setText(e.target.value)}
        onBlur={() => {
          if (text.trim().toUpperCase() !== symbol) {
            setText(symbol)
            setBad(false)
          }
        }}
      />
    </form>
  )
}

export default function ChartPanel({
  symbol,
  timeframe,
  onChange,
  quickButtons = false,
}: {
  symbol: string
  timeframe: Timeframe
  onChange: (symbol: string, timeframe: Timeframe) => void
  quickButtons?: boolean
}) {
  return (
    <div className="chart-panel">
      <div className="chart-toolbar">
        <SymbolBox key={symbol} symbol={symbol} onSymbol={(sym) => onChange(sym, timeframe)} />
        {quickButtons && (
          <div className="segmented small" role="group" aria-label="Quick symbols">
            {QUICK.map((q) => (
              <button key={q} type="button" aria-pressed={symbol === q} onClick={() => onChange(q, timeframe)}>
                {q}
              </button>
            ))}
          </div>
        )}
        <div className="segmented small" role="group" aria-label="Timeframe">
          {TIMEFRAMES.map((tf) => (
            <button key={tf} type="button" aria-pressed={timeframe === tf} onClick={() => onChange(symbol, tf)}>
              {tf}
            </button>
          ))}
        </div>
        <LivePrice symbol={symbol} />
      </div>
      <Chart symbol={symbol} timeframe={timeframe} />
    </div>
  )
}
