import { LOGO } from './brandLogos'
import gis2 from '../assets/brands/2gis.png'
import wolt from '../assets/brands/wolt.jpg'
import instagram from '../assets/brands/instagram.jpg'
import threads from '../assets/brands/threads.avif'

/* Бренд-иконки площадок и соцсетей — реальные скачанные картинки (полное качество).
   У 2ГИС/Wolt фон во всю плитку. У Instagram/Threads вокруг логотипа серые поля —
   подрезаем их лёгким scale-кропом, чтобы плитки читались ровно. */

const IMG: Record<string, string> = { '2gis': gis2, wolt, instagram, threads }
/* коэффициент кропа: >1 обрезает поля вокруг логотипа */
const IMG_SCALE: Record<string, number> = { instagram: 1.32, threads: 1.3 }

export const BRAND_COLOR: Record<string, string> = {
  instagram: '#e1497f', threads: '#d7d9e6', tiktok: '#ff3b5c', googlemaps: '#4c8bf5',
  '2gis': '#19c157', wolt: '#00c2e8', yandex: '#ff3d2e', star: '#e0b341',
}
const WORD: Record<string, string> = { yandex: 'Я', star: '★' }

const norm = (k: string) => k.toLowerCase().replace(/[^a-zа-я0-9]/gi, '')
export function brandKey(platform: string) {
  const k = norm(platform)
  if (k.includes('inst')) return 'instagram'
  if (k.includes('thread')) return 'threads'
  if (k.includes('tik')) return 'tiktok'
  if (k.includes('2gis') || k.includes('гис') || k.includes('2гис')) return '2gis'
  if (k.includes('wolt')) return 'wolt'
  if (k.includes('google')) return 'googlemaps'
  if (k.includes('yandex') || k.includes('яндекс')) return 'yandex'
  return 'star'
}

/* Картинка-логотип в скруглённой плитке (с кропом полей для IG/Threads) */
function Tile({ k, size }: { k: string; size: number }) {
  return (
    <span style={{ width: size, height: size, borderRadius: size * 0.26, flex: 'none', overflow: 'hidden', display: 'block', boxShadow: '0 4px 14px -7px rgba(0,0,0,.6)' }}>
      <img src={IMG[k]} alt={k} width={size} height={size}
        style={{ width: '100%', height: '100%', objectFit: 'cover', transform: `scale(${IMG_SCALE[k] ?? 1})`, display: 'block' }} />
    </span>
  )
}

/* Иконка бренда: реальная картинка → официальный SVG-глиф → вордмарк */
export function Brand({ name, size = 20 }: { name: string; size?: number }) {
  const k = brandKey(name)
  if (IMG[k]) return <Tile k={k} size={size} />
  if (LOGO[k]) {
    return (
      <svg viewBox="0 0 24 24" width={size} height={size} fill="currentColor" aria-hidden>
        <path d={LOGO[k]} />
      </svg>
    )
  }
  return <span style={{ fontSize: size * 0.5, fontWeight: 800, letterSpacing: '-.02em', lineHeight: 1 }}>{WORD[k] ?? '★'}</span>
}

/* App-icon-чип. Картинка — сама плитка; глиф — в фирменном стеклянном чипе. */
export function BrandChip({ name, size = 40 }: { name: string; size?: number }) {
  const k = brandKey(name)
  if (IMG[k]) return <Tile k={k} size={size} />
  const c = BRAND_COLOR[k] ?? 'var(--acc)'
  return (
    <span style={{ width: size, height: size, borderRadius: '28%', flex: 'none', display: 'grid', placeItems: 'center', color: c, background: `color-mix(in srgb, ${c} 15%, transparent)`, border: `1px solid color-mix(in srgb, ${c} 38%, transparent)` }}>
      <Brand name={name} size={LOGO[k] ? size * 0.5 : size} />
    </span>
  )
}
