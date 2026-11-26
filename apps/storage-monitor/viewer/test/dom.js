class FakeClassList {
  constructor(el) { this.el = el; }
  _set(parts) { this.el.className = [...new Set(parts.filter(Boolean))].join(' '); }
  add(...names) { this._set((this.el.className || '').split(/\s+/).concat(names)); }
  remove(...names) { const drop = new Set(names); this._set((this.el.className || '').split(/\s+/).filter(n => !drop.has(n))); }
  contains(name) { return (this.el.className || '').split(/\s+/).includes(name); }
  toggle(name, force) {
    const has = this.contains(name);
    if (force === true || (!has && force !== false)) this.add(name);
    else if (has && force !== true) this.remove(name);
  }
}

class FakeElement {
  constructor(tag = 'div', ownerDocument = null) {
    this.tagName = tag.toUpperCase();
    this.ownerDocument = ownerDocument;
    this.children = [];
    this.style = {};
    this.dataset = {};
    this.className = '';
    this.classList = new FakeClassList(this);
    this.attributes = {};
    this.offsetHeight = 0;
    this.offsetWidth = 0;
    this.clientHeight = 640;
    this.clientWidth = 1000;
    this.hidden = false;
    this.value = '';
    this.listeners = new Map();
    this.tabIndex = undefined;
    this.focused = false;
    this.scrollIntoViewCalls = [];
  }
  appendChild(child) {
    if (!child.ownerDocument) child.ownerDocument = this.ownerDocument;
    this.children.push(child);
    child.parentNode = this;
    return child;
  }
  insertBefore(child,before) {
    if (!before) return this.appendChild(child);
    this.children.splice(this.children.indexOf(before),0,child);
    child.parentNode = this;
    return child;
  }
  get childNodes() { return this.children; }
  replaceChildren(...nodes) { this.children = []; nodes.forEach(node => this.appendChild(node)); }
  removeChild(child) { this.children = this.children.filter(c => c !== child); child.parentNode = null; }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  set innerHTML(value) { this._innerHTML = String(value); this.children = []; }
  get innerHTML() { return this._innerHTML || ''; }
  set textContent(value) { this._textContent = String(value); this.children = []; }
  get textContent() { return this._textContent || ''; }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === 'class') this.className = String(value);
    if (name === 'id') this.id = String(value);
    if (name === 'tabindex') this.tabIndex = Number(value);
    if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, ch) => ch.toUpperCase())] = String(value);
  }
  getAttribute(name) {
    if (name === 'class') return this.className;
    if (name === 'id') return this.id;
    return this.attributes[name];
  }
  addEventListener(type, handler) { this.listeners.set(type, handler); }
  dispatch(type, event = {}) {
    const handler = this.listeners.get(type);
    if (handler) handler(Object.assign({ target: this, currentTarget: this, preventDefault() {} }, event));
  }
  focus() { this.focused = true; if (this.ownerDocument) this.ownerDocument.activeElement = this; }
  scrollIntoView(options) { this.scrollIntoViewCalls.push(options || null); }
  closest(selector) {
    if (!selector || selector[0] !== '.') return null;
    const className = selector.slice(1);
    let cur = this;
    while (cur) {
      if ((cur.className || '').split(/\s+/).includes(className)) return cur;
      cur = cur.parentNode || null;
    }
    return null;
  }
  querySelectorAll() { return []; }
  querySelector() { return null; }
}


export function documentFixture() {
  const elements = new Map();
  const doc = {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id,new FakeElement('div',doc));
      return elements.get(id);
    },
    createElement: tag => new FakeElement(tag,doc)
  };
  doc.documentElement = new FakeElement("html",doc);
  return doc;
}
