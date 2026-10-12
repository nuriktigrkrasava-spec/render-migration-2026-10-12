import { useState } from 'react'

/* Рамка регистрации на демо-ссылке.

   Демо-ссылка показывала выдуманную сеть и на этом заканчивалась: посмотреть
   было можно, а подключить свой iiko — некуда. Теперь по той же ссылке человек
   видит эту рамку, вводит доступы и СВОЙ телеграм, и сразу попадает на свои
   цифры по личной ссылке.

   Телеграм-id у него на этом шаге ещё нет — он появится, когда человек напишет
   боту. Поэтому просим @username: по нему бот и заберёт аккаунт себе. */

type Org = { id: string; name: string }

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    // Лимитер отвечает {error}, а не {detail}, и его текст на английском.
    // «HTTP 429» посреди формы читается как поломка, хотя это «подождите».
    if (res.status === 429) {
      throw new Error('Слишком много попыток подряд. Подождите пятнадцать минут и повторите.')
    }
    const d = data as { detail?: string; error?: string }
    throw new Error(d.detail || d.error || `Не получилось (${res.status})`)
  }
  return data as T
}

const inp: React.CSSProperties = {
  width: '100%', font: 'inherit', fontSize: 14, color: 'var(--hi)', background: 'var(--glass-2)',
  border: '1px solid var(--hair)', borderRadius: 12, padding: '11px 13px', marginTop: 6,
}
const lab: React.CSSProperties = {
  fontSize: 11.5, letterSpacing: '.04em', color: 'var(--mute)', fontWeight: 600, display: 'block',
}

export default function Signup({ onClose }: { onClose: () => void }) {
  const [server, setServer] = useState('')
  const [login, setLogin] = useState('')
  const [pass, setPass] = useState('')
  const [tg, setTg] = useState('')
  const [orgs, setOrgs] = useState<Org[] | null>(null)
  const [org, setOrg] = useState<Org | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [done, setDone] = useState<{ url: string; claim: string } | null>(null)

  async function check() {
    setBusy(true); setErr('')
    try {
      const r = await post<{ valid: boolean; organizations?: Org[]; error?: string }>(
        '/api/setup/validate', { api_login: login, iiko_server: server, api_password: pass })
      if (!r.valid || !r.organizations?.length) throw new Error(r.error || 'iiko не принял доступы')
      setOrgs(r.organizations)
      if (r.organizations.length === 1) setOrg(r.organizations[0])
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) }
  }

  async function save() {
    if (!org) return
    setBusy(true); setErr('')
    try {
      const r = await post<{ url: string; claim_url: string }>('/api/signup', {
        api_login: login, iiko_server: server, api_password: pass,
        organization_id: org.id, organization_name: org.name, tg_username: tg,
      })
      // Телеграм подтверждается переходом по ссылке бота, а не введённым именем:
      // имя публично, и привязка по нему отдала бы аккаунт тому, кто это имя
      // занял. Данные при этом доступны сразу — подтверждение только для пушей.
      setDone({ url: r.url, claim: r.claim_url })
      setBusy(false)
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); setBusy(false) }
  }

  return (
    <div role="dialog" aria-modal="true" aria-label="Подключить свой ресторан"
         style={{ position: 'fixed', inset: 0, zIndex: 80, overflowY: 'auto',
                  background: 'rgba(10,16,30,.5)', backdropFilter: 'blur(6px)',
                  padding: 'clamp(12px,3vw,32px)' }}>
      <div style={{ maxWidth: 460, margin: '0 auto', borderRadius: 22, padding: 'clamp(18px,3vw,26px)',
                    background: 'var(--card)', border: '1px solid var(--hair)',
                    boxShadow: '0 40px 90px -30px rgba(10,20,50,.55)' }}>
        <div style={{ fontSize: 10.5, letterSpacing: '.14em', textTransform: 'uppercase',
                      color: 'var(--mute)', fontWeight: 700 }}>Сейчас вы смотрите демо</div>
        <h2 className="display" style={{ margin: '6px 0 0', fontSize: 'clamp(22px,3vw,28px)',
                                         lineHeight: 1.15, letterSpacing: '-.025em' }}>
          Подключите свой iiko — и увидите свои цифры
        </h2>
        <div style={{ marginTop: 8, fontSize: 13.5, color: 'var(--mute)', lineHeight: 1.5 }}>
          Те же экраны, но по вашей сети: план и факт заказов, ФОТ по явкам, опоздания.
          Доступы читаются только на чтение.
        </div>

        {done ? (
          <>
            <div style={{ marginTop: 16, fontSize: 13.5, color: 'var(--mute)', lineHeight: 1.5 }}>
              Ресторан подключён. Данные уже можно смотреть. Чтобы получать
              уведомления, подтвердите телеграм — одно нажатие.
            </div>
            <a href={done.claim} target="_blank" rel="noreferrer"
               style={{ display: 'block', marginTop: 16, padding: '12px 16px', borderRadius: 13,
                        textAlign: 'center', textDecoration: 'none',
                        background: 'linear-gradient(135deg,var(--acc),var(--acc-2))',
                        color: 'var(--on-acc)', fontSize: 14, fontWeight: 700 }}>
              Подтвердить телеграм
            </a>
            <button onClick={() => { window.location.href = done.url }}
                    style={{ marginTop: 10, width: '100%', padding: '11px 16px', borderRadius: 12,
                             border: '1px solid var(--hair)', background: 'transparent',
                             color: 'var(--hi)', font: 'inherit', fontSize: 13.5, fontWeight: 650,
                             cursor: 'pointer' }}>
              Открыть мои данные
            </button>
          </>
        ) : !orgs ? (
          <>
            <div style={{ marginTop: 16 }}>
              <label style={lab}>Адрес iiko-сервера</label>
              <input style={inp} value={server} onChange={(e) => setServer(e.target.value)}
                     placeholder="https://ваш-сервер.iiko.it" autoComplete="off" />
            </div>
            <div style={{ marginTop: 12 }}>
              <label style={lab}>Логин</label>
              <input style={inp} value={login} onChange={(e) => setLogin(e.target.value)}
                     autoComplete="off" />
            </div>
            <div style={{ marginTop: 12 }}>
              <label style={lab}>Пароль</label>
              <input style={inp} type="password" value={pass}
                     onChange={(e) => setPass(e.target.value)} autoComplete="off" />
            </div>
            <Btn busy={busy} onClick={check}
                 missing={!server ? 'Укажите адрес сервера'
                   : !login ? 'Укажите логин'
                   : !pass ? 'Укажите пароль' : ''}>Проверить доступы</Btn>
          </>
        ) : (
          <>
            <div style={{ marginTop: 16 }}>
              <label style={lab}>Организация — выберите свою</label>
              <select style={inp} value={org?.id || ''}
                      onChange={(e) => setOrg(orgs.find((o) => o.id === e.target.value) || null)}>
                <option value="">Выберите…</option>
                {orgs.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
              </select>
              <div style={{ marginTop: 6, fontSize: 12, color: 'var(--mute)' }}>
                iiko вернул {orgs.length}{orgs.length === 1 ? ' организацию' : ' организаций'} по этим доступам.
              </div>
            </div>
            <div style={{ marginTop: 12 }}>
              <label style={lab}>Ваш телеграм — туда придут уведомления</label>
              <input style={inp} value={tg} onChange={(e) => setTg(e.target.value)}
                     placeholder="@username" autoComplete="off" />
            </div>
            <Btn busy={busy} onClick={save}
                 missing={!org ? 'Выберите организацию из списка выше'
                   : tg.trim().replace(/^@/, '').length < 4
                     ? 'Укажите телеграм — не меньше четырёх знаков' : ''}>
              Открыть мои данные
            </Btn>
          </>
        )}

        {!!err && (
          <div role="alert" style={{ marginTop: 12, fontSize: 13, color: 'var(--red)', lineHeight: 1.45 }}>
            {err}
          </div>
        )}
        {!done && <button onClick={onClose}
                style={{ marginTop: 14, width: '100%', padding: '10px 14px', borderRadius: 12,
                         border: '1px solid var(--hair)', background: 'transparent',
                         color: 'var(--mute)', font: 'inherit', fontSize: 13, cursor: 'pointer' }}>
          Сначала посмотрю демо
        </button>}
      </div>
    </div>
  )
}

/* Кнопка НЕ молчит, когда заблокирована.

   Организаций у сети двадцать три, поэтому список открывается на «Выберите…».
   Раньше до выбора кнопка просто не нажималась: человек жал, ничего не
   происходило, и это читалось как «сайт сломан». Теперь под кнопкой всегда
   написано, чего именно не хватает. */
function Btn({ busy, missing, onClick, children }: {
  busy: boolean; missing?: string; onClick: () => void; children: React.ReactNode
}) {
  const off = busy || !!missing
  return (
    <>
      <button onClick={onClick} disabled={off}
              style={{ marginTop: 16, width: '100%', padding: '12px 16px', borderRadius: 13,
                       border: 'none', background: 'linear-gradient(135deg,var(--acc),var(--acc-2))',
                       color: 'var(--on-acc)', font: 'inherit', fontSize: 14, fontWeight: 700,
                       cursor: off ? 'default' : 'pointer', opacity: off ? 0.55 : 1 }}>
        {busy ? 'Проверяем…' : children}
      </button>
      {!!missing && !busy && (
        <div style={{ marginTop: 7, fontSize: 12.5, color: 'var(--amb)', textAlign: 'center' }}>
          {missing}
        </div>
      )}
    </>
  )
}
