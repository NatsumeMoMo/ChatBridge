(() => {
  const paths = {
    wechat: '<path d="M14.7 5.7C13.5 3.5 10.5 2.5 7.6 3.2 4.2 4 2.1 6.9 2.8 9.9c.3 1.3 1.2 2.4 2.5 3.1l-.5 2.6 2.8-1.4c.7.2 1.4.3 2.1.3"/><path d="M21.5 13.7c0-2.8-2.7-5-6-5s-6 2.2-6 5 2.7 5 6 5c.7 0 1.4-.1 2-.3l2.6 1.3-.5-2.4c1.2-.9 1.9-2.1 1.9-3.6Z"/><path d="M6.8 7.7h.1m4-.1h.1m2 5h.1m4-.1h.1" stroke-width="2.5"/>',
    chat: '<path d="M20 11.5a8 8 0 0 1-8 8 8.8 8.8 0 0 1-3.4-.7L4 20l1.2-4.6a8.8 8.8 0 0 1-.7-3.4 8 8 0 1 1 15.5-.5Z"/><path d="M8.5 11.8h7"/>',
    contacts: '<rect x="4" y="3" width="15" height="18" rx="3"/><path d="M2 7h3m-3 5h3m-3 5h3"/><circle cx="11.5" cy="9" r="2.3"/><path d="M8 16c.4-3 6.6-3 7 0"/>',
    settings: '<path d="m9.5 3-.7 2.3-2 .9-2.2-.6-2.1 3.6 1.6 1.7v2.2l-1.6 1.7 2.1 3.6 2.2-.6 2 .9.7 2.3h4l.7-2.3 2-.9 2.2.6 2.1-3.6-1.6-1.7v-2.2l1.6-1.7-2.1-3.6-2.2.6-2-.9-.7-2.3Z"/><circle cx="11.5" cy="12" r="3"/>',
    back: '<path d="m14.5 5-7 7 7 7"/>',
    search: '<circle cx="10.5" cy="10.5" r="6"/><path d="m15 15 4.5 4.5"/>',
    close: '<path d="m6 6 12 12M18 6 6 18"/>',
    send: '<path d="m5 12 7-7 7 7M12 5v14"/>',
    attach: '<path d="m8 12 6.6-6.6a3 3 0 0 1 4.3 4.3l-8.2 8.2a4.5 4.5 0 0 1-6.4-6.4l8-8"/><path d="m8 12 5.8-5.8a1.5 1.5 0 0 1 2.1 2.1L10 14.2"/>',
    file: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Z"/><path d="M14 3v6h6M8 14h8m-8 3h5"/>',
    image: '<rect x="3" y="3" width="18" height="18" rx="4"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 5-5 4 4 4-6 5 6"/>',
    voice: '<path d="M5 10v4m3-7v10m4-14v18m4-14v10m3-7v4"/>',
    group: '<circle cx="9" cy="8" r="3"/><path d="M3 20v-2a6 6 0 0 1 12 0v2m1-15a3 3 0 0 1 0 6m2 3a5 5 0 0 1 3 4v2"/>',
    chevron: '<path d="m9 5 7 7-7 7"/>',
    shield: '<path d="m12 3 8 3v6c0 5-8 9-8 9S4 17 4 12V6Z"/><path d="m8.5 12 2.3 2.3 4.7-4.7"/>',
    refresh: '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 6a8 8 0 0 1 13 3M5 15a8 8 0 0 0 13 3"/>',
    key: '<circle cx="8" cy="9" r="5"/><path d="m12 13 8 8m-5-5 3-3m0 6 3-3"/>',
    pulse: '<path d="M2 12h5l3-8 4 16 3-8h5"/>',
  };
  function element(tag, text, cls) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  }
  function icon(name) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    for (const [key, value] of Object.entries({viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':'1.6','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'})) node.setAttribute(key, value);
    // 图形仅来自本地常量，平台名称和聊天内容不能成为 SVG 标记。
    node.innerHTML = paths[name] || paths.chat;
    return node;
  }
  function button(text, action, cls) {
    const node = element('button', text, cls);
    node.type = 'button';node.addEventListener('click', action);return node;
  }
  function iconButton(name, label, action, cls = 'iconButton') {
    const node = button(undefined, action, cls);
    node.append(icon(name));node.title = label;node.setAttribute('aria-label', label);return node;
  }
  window.ChatBridgeUI = {element, icon, button, iconButton};
  const factories = new Map();
  // 应用工厂拥有自己的 DOM 和导航，非聊天应用无需套用微信的页面结构。
  window.ChatBridgeApps = {
    register(id, factory) {
      if (!/^[a-z][a-z0-9.-]{0,63}$/.test(id) || typeof factory !== 'function' || factories.has(id)) throw new Error('应用注册无效或重复');
      factories.set(id, factory);
      return () => { if (factories.get(id) === factory) factories.delete(id); };
    },
    get: id => factories.get(id),
  };
})();
