(function (root) {
  'use strict';

  const KEY = 'orientir-v2';
  const OLD_KEY = 'wishmap-v1';
  const ARCHIVE_KEY = 'wishmap-v1-archive';

  const Model = (typeof module !== 'undefined' && module.exports)
    ? require('./model.js')
    : root.Model;

  function available(storage) {
    try {
      storage.setItem(KEY + '-probe', '1');
      storage.removeItem(KEY + '-probe');
      return true;
    } catch (e) {
      return false;
    }
  }

  function archiveOld(storage) {
    /* прежняя карта желаний не выбрасывается: она уезжает в отдельный ключ,
       и страница один раз предлагает скачать её файлом */
    try {
      const старое = storage.getItem(OLD_KEY);
      if (!старое) return null;
      if (!storage.getItem(ARCHIVE_KEY)) storage.setItem(ARCHIVE_KEY, старое);
      storage.removeItem(OLD_KEY);
      return старое;
    } catch (e) {
      return null;
    }
  }

  function fresh(warning, archive) {
    return {
      data: Model.clone(Model.DEFAULT_DATA),
      source: 'default',
      warning: warning || null,
      archive: archive || null
    };
  }

  function load(storage) {
    let archive = null;
    let raw = null;
    try {
      archive = archiveOld(storage);
      raw = storage.getItem(KEY);
    } catch (e) {
      return fresh('Браузер не даёт сохранять на этой странице. Правки не переживут перезагрузку — пользуйтесь кнопкой «Сохранить копию».', null);
    }

    if (!raw) return fresh(null, archive);

    const res = Model.parse(raw);
    if (!res.ok) {
      return fresh('Сохранённые данные повреждены: ' + res.error + '. Открыт чистый лист.', archive);
    }
    return { data: res.data, source: 'storage', warning: null, archive: archive };
  }

  function save(storage, data) {
    try {
      storage.setItem(KEY, Model.serialize(data));
      return true;
    } catch (e) {
      return false;
    }
  }

  function exportName(date) {
    const d = date || new Date();
    return 'orientir-' + d.toISOString().slice(0, 10) + '.json';
  }

  const Store = { KEY, OLD_KEY, ARCHIVE_KEY, available, load, save, exportName };

  if (typeof module !== 'undefined' && module.exports) module.exports = Store;
  else root.Store = Store;
})(typeof globalThis !== 'undefined' ? globalThis : this);
