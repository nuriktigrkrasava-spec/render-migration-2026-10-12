(function (root) {
  'use strict';

  function el(tag, props, kids) {
    const node = document.createElement(tag);
    if (props) {
      for (const key in props) {
        const value = props[key];
        if (value === null || value === undefined || value === false) continue;
        if (key === 'class') node.className = value;
        else if (key === 'text') node.textContent = value;
        else if (key === 'value') node.value = value;
        else if (key.slice(0, 2) === 'on') node.addEventListener(key.slice(2), value);
        else if (value === true) node.setAttribute(key, '');
        else node.setAttribute(key, value);
      }
    }
    for (const kid of (kids || [])) {
      if (kid === null || kid === undefined || kid === false) continue;
      node.appendChild(typeof kid === 'string' ? document.createTextNode(kid) : kid);
    }
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function svg(tag, props) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const key in (props || {})) node.setAttribute(key, props[key]);
    return node;
  }

  root.Dom = { el, clear, svg };
})(typeof globalThis !== 'undefined' ? globalThis : this);
