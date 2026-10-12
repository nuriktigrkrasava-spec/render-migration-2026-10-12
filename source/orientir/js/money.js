(function (root) {
  'use strict';

  const Model = (typeof module !== 'undefined' && module.exports)
    ? require('./model.js')
    : root.Model;

  const KINDS = ['spend', 'income', 'save'];

  function add(data, kind, amount, note, date) {
    const next = Model.clone(data);
    const value = Math.round(Number(amount));
    if (KINDS.indexOf(kind) === -1) return { data: next, id: null };
    if (!Number.isFinite(value) || value <= 0) return { data: next, id: null };
    const entry = {
      id: Model.createId('m'),
      date: Model.isDate(String(date || '').trim()) ? String(date).trim() : Model.today(),
      kind: kind,
      amount: value,
      note: String(note == null ? '' : note).trim()
    };
    next.money.entries.push(entry);
    return { data: next, id: entry.id };
  }

  function remove(data, id) {
    const next = Model.clone(data);
    next.money.entries = next.money.entries.filter(e => e.id !== id);
    return next;
  }

  function setGoal(data, goal) {
    const next = Model.clone(data);
    const value = Math.round(Number(goal));
    next.money.goal = Number.isFinite(value) && value > 0 ? value : 0;
    return next;
  }

  function sumOf(data, kind) {
    return data.money.entries
      .filter(e => e.kind === kind)
      .reduce((total, e) => total + e.amount, 0);
  }

  function pocket(data) {
    return sumOf(data, 'income') - sumOf(data, 'spend') - sumOf(data, 'save');
  }

  function saved(data) { return sumOf(data, 'save'); }

  function spentTotal(data) { return sumOf(data, 'spend'); }

  function spentInMonth(data, month) {
    return data.money.entries
      .filter(e => e.kind === 'spend' && e.date.slice(0, 7) === month)
      .reduce((total, e) => total + e.amount, 0);
  }

  function progress(data) {
    const goal = data.money.goal;
    const now = saved(data);
    const percent = goal > 0 ? Math.min(100, Math.round(now / goal * 100)) : 0;
    return { saved: now, goal: goal, percent: percent };
  }

  function entries(data, limit) {
    const list = data.money.entries.slice().sort((a, b) => (a.date < b.date ? 1 : (a.date > b.date ? -1 : 0)));
    return limit ? list.slice(0, limit) : list;
  }

  function format(amount) {
    const value = Math.round(Number(amount) || 0);
    const digits = String(Math.abs(value)).replace(/\B(?=(\d{3})+(?!\d))/g, '\u00A0');
    /* минус типографский: в интерфейсе он не должен выглядеть дефисом */
    return (value < 0 ? '−' : '') + digits + ' ₽';
  }

  const Money = { KINDS, add, remove, setGoal, pocket, saved, spentTotal, spentInMonth, progress, entries, format };

  if (typeof module !== 'undefined' && module.exports) module.exports = Money;
  else root.Money = Money;
})(typeof globalThis !== 'undefined' ? globalThis : this);
