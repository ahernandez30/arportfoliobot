import type { CloseReason } from '../api'

/** How a trade closed (plan section 6, Account Manager). */
export const REASONS: { value: CloseReason; label: string }[] = [
  { value: 'take_profit', label: 'Take profit' },
  { value: 'stop_loss', label: 'Stop loss' },
  { value: 'signal', label: 'Signal' },
  { value: 'manual', label: 'Manual' },
  { value: 'time', label: 'Time' },
  { value: 'expired', label: 'Expired' },
]
