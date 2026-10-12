(function (root) {
  'use strict';

  const Model = (typeof module !== 'undefined' && module.exports)
    ? require('./model.js')
    : root.Model;

  function cleanSets(sets) {
    if (!Array.isArray(sets)) return [];
    return sets
      .map(s => ({ weight: Number(s && s.weight), reps: Number(s && s.reps) }))
      .filter(s => Number.isFinite(s.weight) && s.weight > 0 && Number.isFinite(s.reps) && s.reps > 0);
  }

  function add(data, input) {
    const next = Model.clone(data);
    const exercise = String((input && input.exercise) || '').trim();
    const sets = cleanSets(input && input.sets);
    if (!exercise || sets.length === 0) return { data: next, id: null };
    const date = String((input && input.date) || '').trim();
    const workout = {
      id: Model.createId('w'),
      date: Model.isDate(date) ? date : Model.today(),
      exercise: exercise,
      sets: sets
    };
    next.workouts.push(workout);
    return { data: next, id: workout.id };
  }

  function remove(data, id) {
    const next = Model.clone(data);
    next.workouts = next.workouts.filter(w => w.id !== id);
    return next;
  }

  function list(data, limit) {
    const all = data.workouts.slice().sort((a, b) => (a.date < b.date ? 1 : (a.date > b.date ? -1 : 0)));
    return limit ? all.slice(0, limit) : all;
  }

  function last(data) {
    const all = list(data, 1);
    return all.length ? all[0] : null;
  }

  function maxWeight(workout) {
    return workout.sets.reduce((top, s) => (s.weight > top ? s.weight : top), 0);
  }

  function exercises(data) {
    const seen = [];
    for (const w of list(data)) {
      if (seen.indexOf(w.exercise) === -1) seen.push(w.exercise);
    }
    return seen;
  }

  function record(data, exercise) {
    const мои = data.workouts.filter(w => w.exercise === exercise);
    if (мои.length === 0) return null;
    return мои.reduce((top, w) => Math.max(top, maxWeight(w)), 0);
  }

  function series(data, exercise) {
    /* одна точка на день: рабочий вес дня — наибольший из подходов */
    const byDate = new Map();
    for (const w of data.workouts) {
      if (w.exercise !== exercise) continue;
      const top = maxWeight(w);
      if (!byDate.has(w.date) || byDate.get(w.date) < top) byDate.set(w.date, top);
    }
    return Array.from(byDate.keys())
      .sort()
      .map(date => ({ date: date, weight: byDate.get(date) }));
  }

  function volume(workout) {
    return workout.sets.reduce((total, s) => total + s.weight * s.reps, 0);
  }

  const Gym = { add, remove, list, last, exercises, record, series, volume, maxWeight };

  if (typeof module !== 'undefined' && module.exports) module.exports = Gym;
  else root.Gym = Gym;
})(typeof globalThis !== 'undefined' ? globalThis : this);
