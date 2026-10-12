import { render, screen } from '@testing-library/react'
import { Delta, Metric, DataTable, Grade, FindingRow, Empty } from './ui'

describe('Delta', () => {
  it('красит рост зелёным и рисует стрелку вверх', () => {
    const { container } = render(<Delta value={21.4} />)
    expect(container.textContent).toContain('▲')
    expect(container.querySelector('.text-pos')).not.toBeNull()
  })
  it('красит падение красным', () => {
    const { container } = render(<Delta value={-34.3} />)
    expect(container.textContent).toContain('▼')
    expect(container.querySelector('.text-neg')).not.toBeNull()
  })
  it('при invert рост считается плохим', () => {
    const { container } = render(<Delta value={6} invert />)
    expect(container.querySelector('.text-neg')).not.toBeNull()
  })
  it('ничего не рисует на пустом значении', () => {
    const { container } = render(<Delta value={null} />)
    expect(container.firstChild).toBeNull()
  })
  it('ничего не рисует на NaN', () => {
    const { container } = render(<Delta value={NaN} />)
    expect(container.firstChild).toBeNull()
  })
  it('на нуле не рисует стрелку и не оставляет пробел перед процентом', () => {
    const { container } = render(<Delta value={0} />)
    expect(container.textContent).not.toContain('▲')
    expect(container.textContent).not.toContain('▼')
    expect(container.textContent).toBe('0,0%')
  })
})

describe('Metric', () => {
  it('показывает подпись и значение', () => {
    render(<Metric label="Средний чек" value="14 759 ₸" />)
    expect(screen.getByText('Средний чек')).toBeTruthy()
    expect(screen.getByText('14 759 ₸')).toBeTruthy()
  })
})

describe('DataTable', () => {
  it('числовым колонкам ставит моноширинный класс и правое выравнивание', () => {
    const { container } = render(
      <DataTable
        columns={[
          { key: 'name', label: 'Блюдо' },
          { key: 'rev', label: 'Выручка', numeric: true },
        ]}
        rows={[['Рибай', '4 200 000 ₸']]}
      />,
    )
    const cells = container.querySelectorAll('tbody td')
    expect(cells[0].className).not.toContain('tnum')
    expect(cells[1].className).toContain('tnum')
    expect(cells[1].className).toContain('text-right')
  })

  it('align: right без numeric выравнивает вправо, но не ставит tnum', () => {
    const { container } = render(
      <DataTable
        columns={[
          { key: 'name', label: 'Блюдо' },
          { key: 'status', label: 'Статус', align: 'right' },
        ]}
        rows={[['Рибай', 'Активно']]}
      />,
    )
    const headers = container.querySelectorAll('thead th')
    const cells = container.querySelectorAll('tbody td')
    expect(headers[1].className).toContain('text-right')
    expect(cells[1].className).toContain('text-right')
    expect(cells[1].className).not.toContain('tnum')
  })
})

describe('Grade', () => {
  it('переводит балл в букву', () => {
    render(<Grade score={72} label="Оценка ресторана" />)
    expect(screen.getByText('C+')).toBeTruthy()
  })
  it('красит зелёным при score >= 75', () => {
    const { container } = render(<Grade score={80} label="Оценка ресторана" />)
    expect(container.querySelector('.text-pos')).not.toBeNull()
  })
  it('красит жёлтым при score в [60, 75)', () => {
    const { container } = render(<Grade score={65} label="Оценка ресторана" />)
    expect(container.querySelector('.text-warn')).not.toBeNull()
  })
  it('красит красным при score < 60', () => {
    const { container } = render(<Grade score={40} label="Оценка ресторана" />)
    expect(container.querySelector('.text-neg')).not.toBeNull()
  })
})

describe('FindingRow', () => {
  it('показывает сумму потерь рядом с заголовком', () => {
    render(<FindingRow finding={{ title: 'Допники в убыток', detail: 'cost 72%', money: 205000 }} />)
    expect(screen.getByText('Допники в убыток')).toBeTruthy()
    expect(screen.getByText('205 000 ₸')).toBeTruthy()
  })
  it('по умолчанию (без tone) красит красным', () => {
    const { container } = render(<FindingRow finding={{ title: 'Т', detail: 'd', money: 1 }} />)
    expect(container.querySelector('.text-neg')).not.toBeNull()
  })
  it('tone: neg красит красным', () => {
    const { container } = render(<FindingRow finding={{ title: 'Т', detail: 'd', money: 1, tone: 'neg' }} />)
    expect(container.querySelector('.text-neg')).not.toBeNull()
  })
  it('tone: warn красит жёлтым', () => {
    const { container } = render(<FindingRow finding={{ title: 'Т', detail: 'd', money: 1, tone: 'warn' }} />)
    expect(container.querySelector('.text-warn')).not.toBeNull()
  })
  it('tone: pos красит зелёным', () => {
    const { container } = render(<FindingRow finding={{ title: 'Т', detail: 'd', money: 1, tone: 'pos' }} />)
    expect(container.querySelector('.text-pos')).not.toBeNull()
  })
})

describe('Empty', () => {
  it('показывает сообщение', () => {
    render(<Empty msg="Нет данных за период" />)
    expect(screen.getByText('Нет данных за период')).toBeTruthy()
  })
})
