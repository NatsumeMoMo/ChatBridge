(() => {
  const {element,icon,button,iconButton} = window.ChatBridgeUI;
  function createMessagingApp(manifest, context) {
    const root = document.getElementById('messagingTemplate').content.firstElementChild.cloneNode(true);
    const $ = id => root.querySelector('#' + id);
    const send = context.send;
    let state = {}, mode = 'recent', route = 'list', selected = '', requested = null, lastSelection = '', catalogKey = '', sending = null, forceBottom = false;
    let contactsRef, conversationsRef, catalogVersion = 0;
    const tabs = {recent:{query:'',scroll:0,limit:250},contacts:{query:'',scroll:0,limit:250}};
    const drafts = new Map(), localEchoes = new Map(), media = new Map(), loading = new Set();
    for (const node of root.querySelectorAll('[data-icon]')) node.prepend(icon(node.dataset.icon));
    root.querySelector('.headerMark').replaceChildren(icon(manifest.id === 'wechat' ? 'wechat' : 'chat'));
    $('browser').setAttribute('aria-label',manifest.name + '列表');
    const extraModules = (manifest.modules || []).filter(id => id !== 'messaging');
    if (extraModules.length) {
      const shortcuts = element('div',undefined,'moduleShortcuts');
      for (const id of extraModules) shortcuts.append(button(id, () => send('openModule',{platformId:manifest.id,moduleId:id})));
      root.querySelector('.bottomTabs').before(shortcuts);
    }
    const newTag = () => Date.now().toString(36) + Math.random().toString(36).slice(2);
    const owns = item => item?.platformId === manifest.id;
    const selection = () => owns(state.selected) ? state.selected : null;
    const current = () => requested || selection();
    function saveList() {tabs[mode].query = $('search').value;tabs[mode].scroll = $('contacts').scrollTop;}
    function setMode(value) {
      saveList();mode = value;$('search').value = tabs[mode].query;render();$('contacts').scrollTop = tabs[mode].scroll;
    }
    function open(item) {
      saveList();requested = {...item,requestId:newTag()};route = 'chat';forceBottom = true;render();$('back').focus({preventScroll:true});
      send('select', {id:item.id,requestId:requested.requestId});
    }
    function back() {
      drafts.set(selected, $('draft').value);route = 'list';requested = null;render();
      $('contacts').scrollTop = tabs[mode].scroll;
      [...$('contacts').children].find(node => node.dataset.contactId === selected)?.focus({preventScroll:true});
    }
    function avatar(item, cls = 'avatar') {
      const node = element('span', undefined, cls);
      const hash = [...(item.name || '')].reduce((n,c) => (n * 31 + c.codePointAt(0)) >>> 0, 0);
      node.dataset.tone = String(hash % 5);
      if (item.kind === 'group') node.append(icon('group'));
      else if (item.nativeId === 'filehelper' || item.name === '文件传输助手') node.append(icon('file'));
      else node.textContent = [...(item.name || '?')][0];
      return node;
    }
    function timeLabel(timestamp) {
      if (!timestamp) return '';
      const date = new Date(timestamp), today = new Date();
      return date.toDateString() === today.toDateString() ? date.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false}) : date.toLocaleDateString('zh-CN',{month:'numeric',day:'numeric'});
    }
    function renderCatalog() {
      const query = $('search').value.trim().toLowerCase();
      const key = [catalogVersion,mode,query,tabs[mode].limit].join('|');
      if (catalogKey === key) return;catalogKey = key;
      let values = (mode === 'recent' ? state.conversations || [] : state.contacts || []).filter(item => owns(item) && (!query || item.name.toLowerCase().includes(query)));
      values = [...values].sort(mode === 'recent' ? (a,b) => (b.lastMessageAt || 0) - (a.lastMessageAt || 0) : (a,b) => a.name.localeCompare(b.name,'zh-CN'));
      $('listCount').textContent = String(values.length);
      const container = $('contacts'), scroll = container.scrollTop;container.replaceChildren();
      for (const item of values.slice(0,tabs[mode].limit)) {
        const row = button(undefined, () => open(item), 'contact');row.dataset.contactId = item.id;
        const body = element('span', undefined, 'contactBody'), heading = element('span', undefined, 'contactHeading');
        heading.append(element('span',item.name,'name'));
        if (mode === 'recent') heading.append(element('time',timeLabel(item.lastMessageAt),'contactTime'));
        else if (item.kind === 'group') heading.append(element('span','群聊','kindBadge'));
        body.append(heading,element('span',mode === 'recent' ? item.preview || (item.kind === 'group' ? '群聊' : '点击查看聊天记录') : item.kind === 'group' ? '进入群聊' : '点击开始聊天','preview'));
        row.append(avatar(item), body);container.append(row);
      }
      if (!values.length) {
        const empty = element('div',undefined,'empty');empty.append(icon(query ? 'search' : mode === 'recent' ? 'chat' : 'contacts'),element('strong',query ? '没有找到相关结果' : mode === 'recent' ? '还没有会话' : '联系人列表为空'),element('p',query ? '试试其他名字或关键词' : mode === 'recent' ? '切换到联系人，开始一段聊天' : '连接后会自动同步联系人'));
        container.append(empty);
      }
      if (values.length > tabs[mode].limit) container.append(button('显示更多联系人', () => {tabs[mode].limit += 250;renderCatalog();}, 'loadMore'));
      container.scrollTop = scroll;
    }
    function render() {
      const actual = selection();
      // 只接受当前点击对应的选择结果；返回后迟到的历史响应不能重新打开聊天页。
      const selectionKey = actual ? [actual.id,state.selectionRevision || 0,state.selectionRequest || ''].join('|') : '';
      if (selectionKey !== lastSelection) {
        lastSelection = selectionKey;
        if (actual && state.selectionRequest == null) {requested = null;route = 'chat';}
      }
      const target = current(), targetId = target?.id || '';
      if (selected !== targetId) {
        drafts.set(selected,$('draft').value);selected = targetId;$('draft').value = drafts.get(selected) || '';$('messages').replaceChildren();forceBottom = true;
      }
      root.dataset.route = route;
      $('browser').hidden = route !== 'list';$('chatPage').hidden = route !== 'chat';
      $('pageTitle').textContent = manifest.name;
      $('listTitle').textContent = mode === 'recent' ? '会话' : '联系人';
      $('search').placeholder = mode === 'recent' ? '搜索会话' : '搜索联系人';
      $('search').setAttribute('aria-label',$('search').placeholder);
      $('clearSearch').hidden = !$('search').value;
      for (const [id,value] of [['recent','recent'],['allContacts','contacts']]) {
        $(id).classList.toggle('active',mode === value);$(id).setAttribute('aria-selected',String(mode === value));
      }
      const account = (state.accounts || []).find(a => a.platformId === manifest.id);
      const connected = state.connection === 'online' && account?.status === 'online';
      $('appConnection').textContent = connected ? '已连接' : state.connection === 'online' ? '等待账号就绪' : state.connection === 'connecting' ? '连接中' : '未连接';
      $('appConnection').dataset.online = String(connected);
      renderCatalog();
      $('conversationTitle').textContent = target?.name || '加载会话';
      $('conversationSubtitle').textContent = target?.kind === 'group' ? '群聊' : manifest.name;
      $('back').setAttribute('aria-label','返回' + (mode === 'recent' ? '会话列表' : '联系人'));
      const available = !!target && actual?.id === target.id;
      const targetAccount = (state.accounts || []).find(a => a.id === target?.accountId);
      $('history').hidden = !state.hasMore || !available;
      $('send').disabled = !!sending || !available || !targetAccount?.capabilities?.['text.send'];
      $('upload').disabled = !available || !targetAccount?.capabilities?.['file.send'];$('draft').disabled = !available;
      renderMessages();resizeDraft();
    }
    function renderMessages() {
      const container = $('messages'), visible = route === 'chat' && root.isConnected && !root.parentElement.hidden;
      const nearBottom = visible && container.scrollHeight - container.scrollTop - container.clientHeight < 80;
      const previousTop = container.scrollTop;
      const anchor = [...container.querySelectorAll('article')].find(node => node.offsetTop + node.offsetHeight >= container.scrollTop);
      const anchorTop = anchor?.offsetTop;
      const dates = new Map([...container.querySelectorAll('[data-date]')].map(node => [node.dataset.date,node]));
      const existing = new Map([...container.querySelectorAll('article')].map(node => [node.dataset.id,node]));
      const native = selection()?.id === selected ? state.messages || [] : [];
      const confirmed = new Set(native.map(message => message.commandId).filter(Boolean));
      const outbox = [...new Map((state.outbox || []).map(message => [message.commandId || message.id,message])).values()].filter(message => message.conversationId === selected && !confirmed.has(message.commandId));
      for (const message of state.outbox || []) if (message.clientTag) localEchoes.delete(message.clientTag);
      for (const [tag,message] of localEchoes) if (confirmed.has(message.commandId)) localEchoes.delete(tag);
      const messages = [...native,...outbox,...[...localEchoes.values()].filter(message => message.conversationId === selected)].sort((a,b) => a.timestamp - b.timestamp || a.id.localeCompare(b.id));
      const nodes = [];let lastDate = '';
      for (const message of messages) {
        const date = new Date(message.timestamp).toLocaleDateString('zh-CN',{year:'numeric',month:'long',day:'numeric'});
        if (date !== lastDate) {const divider = dates.get(date) || element('div',date,'dateDivider');divider.dataset.date = date;nodes.push(divider);lastDate = date;}
        const signature = JSON.stringify(message);let node = existing.get(message.id);
        if (node?.dataset.signature === signature) {nodes.push(node);existing.delete(message.id);continue;}
        node = element('article',undefined,'message ' + message.direction);node.dataset.id = message.id;node.dataset.signature = signature;
        const meta = element('div',undefined,'messageMeta');
        if (message.direction === 'incoming' && current()?.kind === 'group') meta.append(element('span',message.senderName || '群成员','senderName'));
        meta.append(element('time',new Date(message.timestamp).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false})));
        const bubble = element('div',undefined,'bubble');
        if (message.kind === 'text' || !message.attachments?.length) bubble.append(element('div',message.text,'messageText'));
        for (const attachment of message.attachments || []) {
          const part = element('div',undefined,'attachment');part.dataset.attachmentId = attachment.id;part.dataset.kind = attachment.kind;
          const fileHeading = element('div',undefined,'attachmentHeading');fileHeading.append(icon(attachment.kind === 'voice' ? 'voice' : attachment.kind === 'image' ? 'image' : 'file'),element('span',attachment.name || message.text,'attachmentName'));part.append(fileHeading);
          if (attachment.kind === 'image') {const image = element('img');image.alt = '聊天图片';image.hidden = true;part.append(image);}
          else if (attachment.kind === 'voice') {const audio = element('audio');audio.controls = true;audio.preload = 'none';audio.hidden = true;part.append(audio);}
          const actions = element('div',undefined,'attachmentActions');
          if (['ready','preview'].includes(attachment.state)) {
            if (attachment.kind === 'image' || attachment.kind === 'voice') actions.append(button(attachment.kind === 'image' ? '查看图片' : '加载语音', () => loadMedia(attachment)));
            actions.append(button('保存', () => send('save',{id:attachment.id})));
            if (attachment.state === 'preview') part.append(element('span','缩略预览，等待完整内容','subtle'));
          } else {part.append(element('span','等待' + manifest.name + '下载附件','subtle'));actions.append(button('重新检查', () => send('retry',{id:message.id})));}
          part.append(actions);bubble.append(part);applyMedia(part,attachment);
        }
        node.append(meta,bubble);
        const delivery = {persisting:'正在保存待发记录',queued:'等待发送',dispatching:'发送中',submitted:'等待平台回执',uncertain:'发送结果待核对，请勿重复发送',failed:'未保存，草稿已保留',serverAccepted:'服务器已接收'};
        if (message.direction === 'outgoing') node.append(element('div',delivery[message.status] || '待回执','delivery ' + (message.status || '')));
        nodes.push(node);existing.delete(message.id);
      }
      if (!messages.length) {
        const empty = element('div',undefined,'empty chatEmpty');empty.append(icon('chat'),element('strong',selection()?.id === selected ? '开始聊天吧' : '正在加载聊天记录'),element('p','接入后的聊天记录会保存在这里'));nodes.push(empty);
      }
      // 复用已有气泡，消息刷新时不打断正在播放的语音。
      for (let i = 0; i < nodes.length; i++) if (container.children[i] !== nodes[i]) container.insertBefore(nodes[i],container.children[i] || null);
      while (container.children.length > nodes.length) container.lastElementChild.remove();
      if (visible && (nearBottom || forceBottom)) {container.scrollTop = container.scrollHeight;if (messages.length) forceBottom = false;}
      else if (visible && anchor?.isConnected) container.scrollTop = previousTop + anchor.offsetTop - anchorTop;
      else container.scrollTop = previousTop;
    }
    function loadMedia(info) {if (loading.has(info.id + info.sha256)) return;loading.add(info.id + info.sha256);send('media',{id:info.id});}
    function applyMedia(part, info) {
      const data = media.get(info.id + ':' + info.sha256);if (!data) return;
      const target = part.querySelector('img,audio');
      if (target && !target.src) {target.src = `data:${data.contentType};base64,${data.data}`;target.hidden = false;}
    }
    function resizeDraft() {$('draft').style.height = 'auto';$('draft').style.height = Math.min(132,Math.max(42,$('draft').scrollHeight)) + 'px';}
    function handleMessage(message) {
      if (message.type === 'sent') {
        const echo = localEchoes.get(message.clientTag);if (echo) {echo.commandId = message.commandId;echo.status = 'queued';}
        // 确认只清掉当时提交的草稿，保留切换会话或继续输入的新内容。
        if (selected === message.conversationId && $('draft').value === message.text) $('draft').value = '';
        if (drafts.get(message.conversationId) === message.text) drafts.delete(message.conversationId);
        if (!message.clientTag || sending?.clientTag === message.clientTag) sending = null;render();
      }
      if (message.type === 'notice') {loading.clear();render();}
      if (message.type === 'sendFailed') {const echo = localEchoes.get(message.clientTag);if (echo) echo.status = 'failed';if (sending?.clientTag === message.clientTag) sending = null;render();}
      if (message.type === 'media') {
        if (!['image/png','image/jpeg','image/gif','audio/wav'].includes(message.contentType)) return;
        media.set(message.id + ':' + message.sha256,message);loading.delete(message.id + message.sha256);
        while ([...media.values()].reduce((n,item) => n + item.data.length,0) > 48 * 1024 * 1024) media.delete(media.keys().next().value);
        for (const part of root.querySelectorAll('[data-attachment-id]')) if (part.dataset.attachmentId === message.id) applyMedia(part,{id:message.id,sha256:message.sha256});
      }
    }
    $('back').addEventListener('click', back);
    root.addEventListener('keydown', event => {if (event.key === 'Escape' && route === 'chat' && !event.isComposing) {event.preventDefault();back();}});
    $('recent').addEventListener('click', () => setMode('recent'));$('allContacts').addEventListener('click', () => setMode('contacts'));
    root.querySelector('.bottomTabs').addEventListener('keydown', event => {
      if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault();setMode(event.key === 'Home' ? 'recent' : event.key === 'End' ? 'contacts' : mode === 'recent' ? 'contacts' : 'recent');
      $(mode === 'recent' ? 'recent' : 'allContacts').focus();
    });
    $('search').addEventListener('input', () => {tabs[mode].limit = 250;render();});
    $('clearSearch').addEventListener('click', () => {$('search').value = '';render();$('search').focus();});
    $('send').addEventListener('click', () => {
      if ($('send').disabled || !$('draft').value.trim()) return;
      const clientTag = newTag();sending = {conversationId:selected,text:$('draft').value,clientTag};
      // 先回显待发状态，真正的发送成功只能由网关的原生回执确认。
      localEchoes.set(clientTag,{...sending,id:'draft-' + clientTag,kind:'text',direction:'outgoing',senderName:'我',timestamp:Date.now(),status:'persisting',attachments:[]});
      renderMessages();$('messages').scrollTop = $('messages').scrollHeight;send('send',sending);$('send').disabled = true;
    });
    $('draft').addEventListener('keydown', event => {if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {event.preventDefault();$('send').click();}});
    $('draft').addEventListener('input', () => {drafts.set(selected,$('draft').value);resizeDraft();});
    for (const type of ['upload','history']) $(type).addEventListener('click', () => send(type));
    return {
      element:root,
      update(next) {
        if (contactsRef !== next.contacts || conversationsRef !== next.conversations) {catalogVersion++;contactsRef = next.contacts;conversationsRef = next.conversations;}
        state = next;render();
      },
      handleMessage,
      deactivate() {saveList();drafts.set(selected,$('draft').value);for (const audio of root.querySelectorAll('audio')) audio.pause();},
      activate() {if (route === 'chat' && selected) {requested = {...current(),requestId:newTag()};send('select',{id:selected,requestId:requested.requestId});}},
    };
  }
  window.ChatBridgeApps.register('wechat',createMessagingApp);
  window.ChatBridgeApps.register('messaging',createMessagingApp);
})();
