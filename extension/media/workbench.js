(() => {
  const api = acquireVsCodeApi();
  const {element, icon, button, iconButton} = window.ChatBridgeUI;
  const $ = id => document.getElementById(id);
  const send = (type, args = {}) => api.postMessage({type, ...args});
  const apps = new Map();
  const definitions = new Map();
  let state = {}, active = '', settings = false, railKey = '', noticeTimer;
  const labels = {unpaired:'未配对',connecting:'连接中',online:'已连接',offline:'已断开'};
  const accountLabels = {online:'在线',loginRequired:'需要登录',degraded:'接口暂不可用',offline:'离线'};
  function moduleApp(manifest) {
    const root = element('section', undefined, 'modulePage');
    root.append(element('h1', manifest.name), element('p', '选择要使用的功能', 'subtle'));
    for (const id of manifest.modules || []) root.append(button(id, () => send('openModule', {platformId:manifest.id,moduleId:id}), 'moduleCard'));
    return {element:root, update() {}};
  }
  function failedApp(manifest) {
    const root = element('section',undefined,'modulePage');
    root.append(element('h1',manifest.name),element('p','应用界面暂时不可用','subtle'),button('重新打开', () => {apps.set(manifest.id,createApp(manifest));activate(manifest.id);},'moduleCard'));
    return {element:root,update() {}};
  }
  function createApp(manifest) {
    try {
      const factory = window.ChatBridgeApps.get(manifest.id) || (manifest.modules?.includes('messaging') || !manifest.modules ? window.ChatBridgeApps.get('messaging') : moduleApp);
      const app = factory(manifest,{send,showSettings});
      if (!(app?.element instanceof HTMLElement) || typeof app.update !== 'function') throw new Error('Invalid application view');
      return app;
    } catch {return failedApp(manifest);}
  }
  function callApp(id,method,...args) {
    // 单个应用的渲染异常不能锁住应用栏，其他应用和连接设置仍可使用。
    try {return apps.get(id)?.[method]?.(...args);}
    catch {
      const manifest = (state.platforms || []).find(p => p.id === id);if (!manifest) return;
      const fallback = failedApp(manifest);apps.set(id,fallback);
      if (id === active) $('appViewport').replaceChildren(fallback.element);
    }
  }
  function activate(id) {
    const app = apps.get(id);if (!app) return;
    const changed = id !== active;
    if (changed) {callApp(active,'deactivate');active = id;}
    settings = false;if ($('appViewport').firstElementChild !== app.element) $('appViewport').replaceChildren(app.element);
    if (changed) {send('selectPlatform', {platformId:id});callApp(id,'activate');}
    render();
  }
  function showSettings(value = !settings) {
    settings = value;render();if (settings) $('settingsBack').focus();
  }
  function renderRail() {
    const manifests = state.platforms || [];
    const key = JSON.stringify(manifests.map(p => [p.id,p.name,p.modules]));
    if (key !== railKey) {
      railKey = key;
      for (const [id] of apps) {
        const manifest = manifests.find(p => p.id === id);
        if (!manifest || definitions.get(id) !== JSON.stringify([manifest.name,manifest.modules])) {callApp(id,'dispose');apps.delete(id);definitions.delete(id);}
      }
      for (const manifest of manifests) if (!apps.has(manifest.id)) {
        apps.set(manifest.id,createApp(manifest));definitions.set(manifest.id,JSON.stringify([manifest.name,manifest.modules]));
      }
      $('appList').replaceChildren(...manifests.map(p => {
        const node = iconButton(p.id === 'wechat' ? 'wechat' : 'chat', p.name, () => activate(p.id), 'appButton');
        node.dataset.appId = p.id;return node;
      }));
    }
    if (active && !apps.has(active)) active = '';
    if (!active && manifests.length) {
      active = manifests.find(p => p.id === 'wechat')?.id || manifests[0].id;
    }
    if (active && $('appViewport').firstElementChild !== apps.get(active).element) $('appViewport').replaceChildren(apps.get(active).element);
    for (const node of $('appList').children) {
      node.classList.toggle('active', node.dataset.appId === active);
      node.setAttribute('aria-pressed', String(node.dataset.appId === active));
    }
  }
  function render() {
    renderRail();$('appViewport').hidden = settings || !active;$('welcome').hidden = settings || !!active;$('settingsPage').hidden = !settings;
    $('settings').classList.toggle('active', settings);$('settings').setAttribute('aria-pressed', String(settings));
    $('status').textContent = labels[state.connection] || '准备中';$('status').dataset.connection = state.connection || '';$('connectionDot').dataset.connection = state.connection || '';
    const connectionTitle = '连接状态：' + $('status').textContent;
    $('connectionDot').title = connectionTitle;$('connectionDot').setAttribute('aria-label', connectionTitle);
    $('accountStatus').textContent = (state.accounts || []).map(a => a.name + ' · ' + (accountLabels[a.status] || a.status)).join(' / ') || state.detail || '连接自己的聊天网关';
    $('connectionDetail').textContent = state.detail || (state.connection === 'online' ? '设备已连接，消息同步中' : '可重新连接或导入设备配对');
    $('pending').replaceChildren();
    if (state.pending) $('pending').append(element('span', `${state.pending} 条待发，恢复连接后继续`), button('管理', () => send('cancelPending')));
    callApp(active,'update',state);
  }
  window.addEventListener('message', event => {
    const message = event.data;
    // 网关目录按变化增量下发，消息更新不能清空已有应用和联系人。
    if (message.type === 'state') {state = {...state,...message};render();}
    else {
      for (const [id] of apps) callApp(id,'handleMessage',message);
      if (message.type === 'notice') {
        $('noticeText').textContent = message.text;$('notice').hidden = false;
        clearTimeout(noticeTimer);noticeTimer = setTimeout(() => {$('notice').hidden = true;}, 7000);
      }
    }
  });
  for (const node of document.querySelectorAll('[data-icon]')) node.prepend(icon(node.dataset.icon));
  $('settings').addEventListener('click', () => showSettings());$('connectionDot').addEventListener('click', () => showSettings(true));
  $('settingsBack').addEventListener('click', () => showSettings(false));
  $('noticeClose').addEventListener('click', () => {$('notice').hidden = true;clearTimeout(noticeTimer);});
  for (const type of ['reconnect','diagnostics','pair']) $(type).addEventListener('click', () => send(type));
  document.addEventListener('keydown', event => {if (event.key === 'Escape' && settings) {event.preventDefault();showSettings(false);$('settings').focus();}});
  render();send('ready');
})();
