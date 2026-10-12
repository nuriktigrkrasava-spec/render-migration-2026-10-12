export const IC: Record<string, string> = {
  ai: '<path d="M12 3l1.8 4.7L18 9.5l-4.2 1.8L12 16l-1.8-4.7L6 9.5l4.2-1.8z"/><path d="M18 15l.9 2.1L21 18l-2.1.9L18 21l-.9-2.1L15 18l2.1-.9z"/>',
  dash: '<rect x="3" y="3" width="7" height="9" rx="1.6"/><rect x="14" y="3" width="7" height="5" rx="1.6"/><rect x="14" y="11" width="7" height="10" rx="1.6"/><rect x="3" y="15" width="7" height="6" rx="1.6"/>',
  fc: '<path d="M3 3v18h18"/><path d="M7 14l4-4 3 3 5-6"/>',
  exp: '<rect x="3" y="6" width="18" height="13" rx="2.5"/><path d="M3 10h18"/>',
  inv: '<path d="M5 3h10l4 4v14H5z"/><path d="M9 9h6M9 13h6M9 17h4"/>',
  yoy: '<path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 4v4h-4"/>',
  staff: '<circle cx="9" cy="8" r="3.2"/><path d="M3.5 20a6 6 0 0 1 11 0"/><path d="M16 5.2a3 3 0 0 1 0 5.6"/><path d="M15.5 20a6 6 0 0 0 5-3"/>',
  anom: '<path d="M12 3l9 16H3z"/><path d="M12 10v4M12 17h.01"/>',
  soc: '<circle cx="6" cy="12" r="2.4"/><circle cx="18" cy="6" r="2.4"/><circle cx="18" cy="18" r="2.4"/><path d="M8 11l8-4M8 13l8 4"/>',
  mocks: '<rect x="3" y="4" width="7" height="16" rx="2"/><rect x="13" y="4" width="8" height="16" rx="3"/>',
  seat: '<circle cx="12" cy="9" r="4"/><rect x="5" y="15" width="14" height="5" rx="2"/><path d="M8 15v-1M16 15v-1"/>',
  island: '<path d="M12 3l7 5-7 5-7-5z"/><path d="M5 12l7 5 7-5"/><path d="M5 16l7 5 7-5"/>',
  clients: '<circle cx="9" cy="8" r="3.2"/><path d="M3.5 20a6 6 0 0 1 11 0"/><path d="M17 13.5c1.6.9 2.7 2 3.5 3.5"/><path d="M17 4l1.2 2.6L21 7.8l-2.4 1.4L17 12l-1.2-2.8L13 7.8l2.8-1.2z"/>',
  menu: '<rect x="4" y="3" width="16" height="18" rx="2.5"/><path d="M8 8h8M8 12h8M8 16h5"/>',
  supply: '<path d="M3 8l9-4 9 4-9 4z"/><path d="M3 8v8l9 4 9-4V8"/><path d="M16 15h5M18.5 12.5l2.5 2.5-2.5 2.5"/>',
  rep: '<path d="M12 3l2.6 5.6 6 .7-4.4 4.1 1.2 5.9L12 16.9 6.6 19.3l1.2-5.9L3.4 9.3l6-.7z"/>',
  feed: '<rect x="3.5" y="4" width="17" height="5" rx="1.6"/><rect x="3.5" y="11" width="17" height="5" rx="1.6"/><path d="M6.5 6.5h5M6.5 13.5h5"/>',
  ask: '<path d="M4 5.5h16v10H9l-4 3.5v-3.5H4z"/><path d="M8.5 10.5h7M8.5 7.5h4"/>',
  more: '<circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/>',
  sched: '<rect x="3" y="4.5" width="18" height="16" rx="2.5"/><path d="M3 9.5h18M8 3v3M16 3v3"/>',
  link: '<path d="M9.5 14.5l5-5"/><path d="M12 6.5l1-1a4 4 0 0 1 5.7 5.7l-1 1"/><path d="M12 17.5l-1 1a4 4 0 0 1-5.7-5.7l1-1"/>',
}

export function Icon({ k, size = 17 }: { k: string; size?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.7"
      strokeLinecap="round" strokeLinejoin="round" dangerouslySetInnerHTML={{ __html: IC[k] || '' }} />
  )
}
