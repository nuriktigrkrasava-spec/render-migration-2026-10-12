/** Единственный источник цветов для recharts. В JSX экранов цвета не хардкодить. */
export const CHART = {
  ink: '#17171a',
  neg: '#c8562f',
  pos: '#2f7a52',
  warn: '#b8860b',
  muted: '#8b8574',
  rule: '#ded8cc',
  paper: '#faf7f2',
  paper2: '#f2ede4',

  /** подписи осей */
  axis: {
    fill: '#8b8574',
    fontSize: 10,
    fontFamily: '"Golos Text", sans-serif',
  },

  /** горизонтальная сетка — волосяная, вертикальной нет */
  grid: {
    stroke: '#ded8cc',
    strokeWidth: 1,
  },

  /** всплывающая подсказка: бумага с чернильной рамкой, без скруглений и теней */
  tooltip: {
    background: '#faf7f2',
    border: '1px solid #17171a',
    borderRadius: 0,
    color: '#17171a',
    fontFamily: '"Golos Text", sans-serif',
    fontSize: 12,
    boxShadow: 'none',
  },
} as const
