import { Glass } from './ui'
import { useApi } from '../lib/useApi'
import { tenge } from '../lib/format'

/* Трекер задач из бота: какие филиалы отметили выполнение, какие молчат.
   Сводка по филиалам, а не лента нажатий — владельцу важно «кто закрыл». */

type Task = { id: number; title: string; status: string; money: number; answered_by: string | null }
type Branch = { branch: string; done: number; later: number; open: number; money_open: number; tasks: Task[] }
type TasksT = { branches: Branch[]; total: number; done: number; open: number; later: number; done_pct: number; money_open: number }

const dot = (s: string) => (s === 'done' ? 'var(--grn)' : s === 'later' ? 'var(--amb)' : 'var(--red)')

export default function TaskTracker() {
  const { data, error, loading } = useApi<TasksT>('tasks')
  if (loading || error || !data || !data.total) return null   // нечего показывать — не занимаем место

  const tone = data.done_pct >= 70 ? 'var(--grn)' : data.done_pct >= 40 ? 'var(--amb)' : 'var(--red)'
  return (
    <Glass span={12}>
      <div className="eyebrow">
        <span>Задачи из бота</span>
        <span className="hint">кто отметил выполнение · за 14 дней</span>
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 24, alignItems: 'baseline', marginTop: 12 }}>
        <div>
          <div className="tnum" style={{ fontSize: 32, fontWeight: 680, letterSpacing: '-.03em', color: tone }}>
            {data.done}/{data.total}
          </div>
          <div style={{ fontSize: 11.5, color: 'var(--mute)', marginTop: 2 }}>выполнено · {data.done_pct}%</div>
        </div>
        {data.open > 0 && (
          <div>
            <div className="tnum" style={{ fontSize: 20, fontWeight: 660, color: 'var(--red)' }}>{data.open}</div>
            <div style={{ fontSize: 11.5, color: 'var(--mute)', marginTop: 2 }}>без ответа</div>
          </div>
        )}
        {data.money_open > 0 && (
          <div>
            <div className="tnum" style={{ fontSize: 20, fontWeight: 660 }}>{tenge(data.money_open)}</div>
            <div style={{ fontSize: 11.5, color: 'var(--mute)', marginTop: 2 }}>на кону в незакрытых</div>
          </div>
        )}
      </div>

      {/* по одной строке на филиал — без ленты сообщений */}
      <div style={{ marginTop: 14 }}>
        {data.branches.map((b) => (
          <div key={b.branch} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 0', borderTop: '1px solid var(--hair-2)' }}>
            <span style={{ width: 7, height: 7, borderRadius: '50%', flex: 'none', background: b.open ? 'var(--red)' : b.later ? 'var(--amb)' : 'var(--grn)' }} />
            <span style={{ fontSize: 13, color: 'var(--hi)', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {b.branch}
            </span>
            <span style={{ display: 'flex', gap: 4, flex: 'none' }}>
              {b.tasks.slice(0, 8).map((t) => (
                <span key={t.id} title={`${t.title} — ${t.status === 'done' ? 'сделано' : t.status === 'later' ? 'отложено' : 'без ответа'}`}
                  style={{ width: 8, height: 8, borderRadius: 2, background: dot(t.status) }} />
              ))}
            </span>
            <span className="tnum" style={{ fontSize: 12, color: 'var(--mute)', flex: 'none', minWidth: 62, textAlign: 'right' }}>
              {b.done}/{b.tasks.length}
            </span>
          </div>
        ))}
      </div>
    </Glass>
  )
}
