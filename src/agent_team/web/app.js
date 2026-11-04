'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const get = (id, key, fallback='') => ($(id)?.querySelector(`[name="${key}"]`)?.value ?? fallback).trim();
const checked = (id, name) => Array.from($(id)?.querySelectorAll(`input[name="${name}"]:checked`) ?? []).map(x => x.value);
const avatar = a => esc(a?.avatar || '🤖');
const dateTime = iso => iso ? new Date(iso).toLocaleTimeString('zh-CN', {hour:'2-digit',minute:'2-digit'}) : '';
const modeNames = {mention:'@点名 · 默认由首位成员回复',roundtable:'圆桌 · 所有成员依次发言（最多 4 位）',cooperate:'协作 · 最多 4 位成员接力完善答案'};
let state = {conversations:[], agents:[], credentials:[], skills:[], providers:{}};
let current = null, busy = false, toastTimer;

function toast(message, error=false) {
  const el = $('toast'); el.textContent=message; el.className=`toast visible${error?' error':''}`;
  clearTimeout(toastTimer); toastTimer=setTimeout(()=>el.className='toast',5000);
}
async function api(path, options={}) {
  const headers = {'Content-Type':'application/json', ...(options.headers || {})};
  const token = sessionStorage.getItem('agentTeamToken'); if(token)headers['X-Agent-Team-Token']=token;
  let response;
  try {response=await fetch('/api'+path,{...options,headers});}
  catch {throw new Error('无法连接服务端，请检查网络和服务是否启动。');}
  let result={}; try {result=await response.json();} catch {}
  if (response.status === 401) {
    const newToken=window.prompt('请输入 AGENT_TEAM_ACCESS_TOKEN 访问口令：');
    if(newToken) {sessionStorage.setItem('agentTeamToken',newToken);return api(path,options);}
    throw new Error('访问口令不正确或未提供');
  }
  if(!response.ok) {
    const detail=result.detail;
    throw new Error(typeof detail==='string'?detail: `请求失败（HTTP ${response.status}）`);
  }
  return result;
}
async function refresh() {state=await api('/state');renderSidebar();if(current)renderHeader();}
function conversationById(id) {return state.conversations.find(x=>x.id===id);}
function agentById(id) {return state.agents.find(x=>x.id===id);}
function credentialById(id){return state.credentials.find(x=>x.id===id);}
function renderSidebar() {
  $('chat-count').textContent=state.conversations.length;
  $('conversation-list').innerHTML=state.conversations.length ? state.conversations.map(c => `
  <button class="nav-item ${c.id===current?'active':''}" data-chat="${esc(c.id)}">
    <span class="item-icon ${c.kind==='group'?'group':''}">${c.kind==='group'?'👥':avatar(agentById(c.agent_ids[0]))}</span>
    <span class="nav-text"><div class="nav-title">${esc(c.name)}</div><div class="nav-subtitle">${esc(c.preview || (c.kind==='group' ? `${c.agent_ids.length} 位成员` : '开始对话'))}</div></span>
  </button>`).join('') : '<p class="muted" style="padding:0 12px;font-size:12px">还没有聊天，点击上方开始。</p>';
  $('agent-list').innerHTML=state.agents.map(a => `<button class="agent-item" data-direct="${esc(a.id)}"><span class="item-icon">${avatar(a)}</span>
    <span class="nav-text"><div class="nav-title">${esc(a.name)}</div><div class="nav-subtitle">${esc(a.remark || a.model)}${a.credential_id==='demo'?' · 演示':''}</div></span></button>`).join('');
}
function renderHeader() {
  const c=conversationById(current); if(!c)return;
  $('conversation-title').textContent=c.name;
  $('conversation-subtitle').textContent=c.kind==='direct'
    ? `${agentById(c.agent_ids[0])?.remark || '私聊'} · ${agentById(c.agent_ids[0])?.model || '未配置模型'}`
    : `${c.agent_ids.map(id=>agentById(id)?.name).filter(Boolean).join('、')} · ${modeNames[c.mode]}`;
  $('mode-tip').textContent=c.kind==='group'?modeNames[c.mode]:'私聊 · 一个智能体对应一套角色与模型配置';
  const edit=$('edit-agent');edit.hidden=c.kind!=='direct';edit.dataset.agent=c.agent_ids[0]||'';
}
function renderMessages(messages) {
  const el=$('messages');
  el.innerHTML=messages.length ? messages.map(m => {
    const isUser=m.sender==='user',a=agentById(m.agent_id);
    return `<article class="message ${isUser?'user':'agent'}"><div class="msg-avatar">${isUser?'你':avatar(a)}</div>
      <div class="msg-content"><div class="msg-meta"><b>${isUser?'你':esc(m.agent_name || a?.name || '智能体')}</b><time>${esc(dateTime(m.created_at))}</time></div>
      <div class="msg-bubble">${esc(m.content)}</div></div></article>`;
  }).join('') : '<div class="empty-messages"><strong>开启一段新对话</strong>像和朋友聊天一样发送消息；群聊可 @成员 点名，或启用协作模式。</div>';
  el.scrollTop=el.scrollHeight;
}
async function openChat(id) {
  current=id; $('welcome').hidden=true; $('messages').hidden=false; $('chat-bottom').hidden=false;
  $('message-input').value='';renderSidebar();renderHeader();closeSidebar();
  try {renderMessages((await api(`/conversations/${encodeURIComponent(id)}/messages`)).messages);}
  catch(e){toast(e.message,true);}
}
async function startDirect(agentId) {
  const existing=state.conversations.find(c=>c.kind==='direct' && c.agent_ids[0]===agentId);
  if(existing)return openChat(existing.id);
  const a=agentById(agentId); if(!a)return;
  try {const c=await api('/conversations',{method:'POST',body:JSON.stringify({name:a.name,kind:'direct',mode:'mention',agent_ids:[a.id]})});
    await refresh();await openChat(c.id);}catch(e){toast(e.message,true);}
}
async function send(text) {
  if(!current || busy || !text.trim())return;
  busy=true; $('send-button').disabled=true;$('message-input').disabled=true;
  const before=$('message-input').value; $('message-input').value='';
  const list=$('messages');const temp=document.createElement('div');temp.className='empty-messages';temp.textContent='智能体正在思考…';list.appendChild(temp);list.scrollTop=list.scrollHeight;
  try {
    const result=await api(`/conversations/${encodeURIComponent(current)}/messages`,{method:'POST',body:JSON.stringify({text})});
    const msgs=await api(`/conversations/${encodeURIComponent(current)}/messages`);
    renderMessages(msgs.messages);
    if(result.errors?.length)toast(result.errors.join('；'),true);
    await refresh();
  }catch(e){$('message-input').value=before;toast(e.message,true);try{renderMessages((await api(`/conversations/${encodeURIComponent(current)}/messages`)).messages);}catch{}}
  finally{busy=false;$('send-button').disabled=false;$('message-input').disabled=false;$('message-input').focus();}
}
function modal(title,subtitle,body) {
  $('dialog-body').innerHTML=`<div class="modal"><div class="modal-head"><div><h2>${esc(title)}</h2><p>${esc(subtitle)}</p></div><button type="button" data-close aria-label="关闭">✕</button></div>${body}</div>`;
  $('dialog').showModal();
}
function closeModal(){if($('dialog').open)$('dialog').close();}
function selectOptions(items, selected, valueKey='id',labelKey='name'){
  return items.map(x=>`<option value="${esc(x[valueKey])}" ${x[valueKey]===selected?'selected':''}>${esc(x[labelKey])}</option>`).join('');
}
function renderConversationModal() {
  if(!state.agents.length){toast('请先创建一个智能体',true);return;}
  modal('新建聊天 / 群组','一个群组可以包含多个拥有不同模型和技能的智能体。', `
    <form id="create-conversation-form">
      <div class="field"><label>聊天名称</label><input name="name" placeholder="例如：项目讨论组" maxlength="80" required></div>
      <div class="field"><label>聊天类型</label><select name="kind"><option value="direct">私聊</option><option value="group" selected>群聊</option></select></div>
      <div class="field"><label>参与智能体（私聊只选一位）</label><div class="checks">${state.agents.map((a,i)=>`<label><input name="member" type="checkbox" value="${esc(a.id)}" ${i<2?'checked':''}> ${avatar(a)} ${esc(a.name)} <span class="muted">${esc(a.remark)}</span></label>`).join('')}</div></div>
      <div class="field"><label>群聊运行模式</label><select name="mode">
        <option value="mention">@点名（不指定则首位回复，最省费用）</option>
        <option value="roundtable">圆桌群聊（所有成员依次发言）</option>
        <option value="cooperate" selected>协同合作（按顺序接力完善答案）</option>
      </select></div>
      <div class="hint">为防止自动刷屏，每条消息最多触发 4 位智能体各回复一次。每次模型调用都可能单独计费。</div>
      <div class="form-actions"><button type="button" class="outline-button" data-close>取消</button><button class="primary-button" type="submit">创建聊天</button></div>
    </form>`);
  $('create-conversation-form').addEventListener('submit',async e=>{
    e.preventDefault();const kind=get('create-conversation-form','kind'),ids=checked('create-conversation-form','member');
    if((kind==='direct'&&ids.length!==1)||!ids.length){toast('私聊必须选择 1 位智能体，群聊至少选择 1 位',true);return;}
    const name=get('create-conversation-form','name');const payload={name,kind,mode:kind==='direct'?'mention':get('create-conversation-form','mode'),agent_ids:ids};
    try{const c=await api('/conversations',{method:'POST',body:JSON.stringify(payload)});closeModal();await refresh();await openChat(c.id);}catch(err){toast(err.message,true);}
  });
}
function renderAgentModal(agentId=null){
  const a=agentId?agentById(agentId):null;
  if(!state.credentials.length){toast('请先配置模型凭据',true);return;}
  modal(a?'编辑智能体':'创建智能体','每个成员都可以拥有独立名字、角色、模型和 Skill。', `
    <form id="agent-form">
      <div class="field-grid"><div class="field"><label>名称</label><input name="name" required maxlength="32" value="${esc(a?.name||'') }" placeholder="例如：小研"></div>
      <div class="field"><label>备注 / 分工</label><input name="remark" maxlength="100" value="${esc(a?.remark||'')}" placeholder="例如：资深研究员"></div></div>
      <div class="field"><label>头像（一个 Emoji）</label><input name="avatar" maxlength="8" value="${esc(a?.avatar||'🤖')}"></div>
      <div class="field-grid"><div class="field"><label>模型凭据</label><select name="credential_id">${selectOptions(state.credentials,a?.credential_id||'demo')}</select></div>
      <div class="field"><label>模型 ID</label><input name="model" required maxlength="160" value="${esc(a?.model||'demo')}" placeholder="例如：deepseek-chat"></div></div>
      <div class="field"><label>人设 / 系统提示词</label><textarea name="system_prompt" maxlength="10000" placeholder="描述此智能体的职责、个性和回答方式">${esc(a?.system_prompt||'')}</textarea></div>
      <div class="field"><label>已安装的文本型 Skills</label><div class="checks">${state.skills.length?state.skills.map(s=>`<label><input type="checkbox" name="skill" value="${esc(s.id)}" ${a?.skill_ids?.includes(s.id)?'checked':''}>${esc(s.name)} <span class="muted">${esc(s.description)}</span></label>`).join(''):'<div class="muted">暂无 Skill，可在侧栏 Skills 中新增。</div>'}</div></div>
      <div class="form-actions"><button type="button" class="outline-button" data-close>取消</button><button class="primary-button" type="submit">${a?'保存修改':'创建智能体'}</button></div>
    </form>`);
  $('agent-form').addEventListener('submit',async e=>{e.preventDefault();const form='agent-form';
    const payload={name:get(form,'name'),remark:get(form,'remark'),avatar:get(form,'avatar')||'🤖',system_prompt:get(form,'system_prompt'),credential_id:get(form,'credential_id'),model:get(form,'model'),skill_ids:checked(form,'skill')};
    try{await api(a?`/agents/${encodeURIComponent(a.id)}`:'/agents',{method:a?'PUT':'POST',body:JSON.stringify(payload)});closeModal();await refresh();toast(a?'智能体已更新':'智能体已创建');}
    catch(err){toast(err.message,true);}
  });
}
function renderCredentialsModal(){
  const creds=state.credentials.map(c=>`<div class="stack-row"><div><b>${esc(c.name)}</b><small>${esc(c.provider)} · ${esc(c.base_url||'离线演示')} ${c.has_key?'· 已保存 Key':'· 无 Key'}</small></div><span class="tag">${c.provider==='demo'?'演示':'已配置'}</span></div>`).join('');
  modal('模型与凭据','一个凭据可以同时被多个智能体使用，密钥只保存在服务端。', `
    <div class="stack-list">${creds}</div><hr class="line">
    <form id="credential-form"><div class="field"><label>凭据名称</label><input name="name" required maxlength="80" placeholder="例如：我的 DeepSeek Key"></div>
    <div class="field"><label>服务商</label><select name="provider">${Object.keys(state.providers).filter(x=>x!=='demo').map(p=>`<option value="${esc(p)}">${esc(p)}</option>`).join('')}</select></div>
    <div class="field"><label>API Base URL</label><input name="base_url" required maxlength="500" placeholder="https://.../v1"><small>支持 OpenAI、DeepSeek、OpenRouter、Claude、Gemini、Ollama 和兼容接口。</small></div>
    <div class="field"><label>API Key</label><input name="api_key" type="password" autocomplete="off" maxlength="1500" placeholder="不会在读取列表时返回"><small>本机加密保存。Ollama 本地模式可以留空。</small></div>
    <div class="form-actions"><button type="button" class="outline-button" data-close>取消</button><button class="primary-button" type="submit">保存凭据</button></div></form>`);
  const f='credential-form';const select=$(f).querySelector('[name="provider"]');const url=$(f).querySelector('[name="base_url"]');
  const setDefault=()=>url.value=state.providers[select.value]||'';select.addEventListener('change',setDefault);setDefault();
  $(f).addEventListener('submit',async e=>{e.preventDefault();const data={name:get(f,'name'),provider:get(f,'provider'),base_url:get(f,'base_url'),api_key:get(f,'api_key')};
    try{await api('/credentials',{method:'POST',body:JSON.stringify(data)});closeModal();await refresh();toast('凭据保存成功，可以分配给任意智能体');}
    catch(err){toast(err.message,true);}
  });
}
function renderSkillsModal(){
  modal('Skills · 能力设定','第一阶段支持安全的文本指令型 Skill；不执行第三方任意代码。', `
    <div class="stack-list">${state.skills.length?state.skills.map(s=>`<div class="stack-row"><div><b>${esc(s.name)}</b><small>${esc(s.description)}</small></div><span class="tag">Prompt Skill</span></div>`).join(''):'<p class="muted">暂无 Skills。创建后可以在「编辑智能体」中选择。</p>'}</div>
    <hr class="line"><form id="skill-form">
      <div class="field"><label>Skill 名称</label><input name="name" required maxlength="60" placeholder="例如：科研论文分析"></div>
      <div class="field"><label>简介</label><input name="description" maxlength="200" placeholder="例如：分析论文的证据链与局限"></div>
      <div class="field"><label>指令（Prompt 型，不支持代码执行）</label><textarea name="instructions" required maxlength="12000" placeholder="说明任务步骤、输出格式、注意事项"></textarea></div>
      <div class="field"><label>从本地 SKILL.md / .txt 导入文本指令（可选）</label><input type="file" name="skill_file" accept=".md,.txt,text/markdown,text/plain"><small>仅提取文本，不执行导入文件中的脚本。</small></div>
      <div class="hint">可执行插件（文件系统、浏览器、Shell、MCP）尚未启用。后续将加入权限申请与执行沙箱，不能把文本 Skill 等同于插件。</div>
      <div class="form-actions"><button type="button" class="outline-button" data-close>关闭</button><button class="primary-button" type="submit">新增 Skill</button></div></form>`);
  $('skill-form').querySelector('[name="skill_file"]').addEventListener('change', async e => {
    const file=e.target.files?.[0]; if(!file)return;
    if(file.size>50000){toast('Skill 文件超过 50 KB，请缩小后导入',true);return;}
    const content=(await file.text()).slice(0,12000);
    $('skill-form').querySelector('[name="instructions"]').value=content;
    if(!get('skill-form','name'))$('skill-form').querySelector('[name="name"]').value=file.name.replace(/\.(md|txt)$/i,'').slice(0,60);
    toast('已加载本地 Skill 文本，请检查内容后保存');
  });
  $('skill-form').addEventListener('submit',async e=>{e.preventDefault();const f='skill-form';
    try{await api('/skills',{method:'POST',body:JSON.stringify({name:get(f,'name'),description:get(f,'description'),instructions:get(f,'instructions')})});closeModal();await refresh();toast('Skill 已创建，可以在智能体设置中安装');}
    catch(err){toast(err.message,true);}
  });
}
function openSidebar(){$('shell').classList.add('sidebar-open');}
function closeSidebar(){$('shell').classList.remove('sidebar-open');}
async function startup(){
  $('new-chat').addEventListener('click',renderConversationModal);
  $('welcome-chat').addEventListener('click',renderConversationModal);
  $('new-agent').addEventListener('click',()=>renderAgentModal());
  $('welcome-agent').addEventListener('click',()=>renderAgentModal());
  $('manage-credentials').addEventListener('click',renderCredentialsModal);
  $('manage-skills').addEventListener('click',renderSkillsModal);
  $('edit-agent').addEventListener('click',()=>renderAgentModal($('edit-agent').dataset.agent));
  $('conversation-list').addEventListener('click',e=>{const b=e.target.closest('[data-chat]');if(b)openChat(b.dataset.chat);});
  $('agent-list').addEventListener('click',e=>{const b=e.target.closest('[data-direct]');if(b)startDirect(b.dataset.direct);});
  $('dialog').addEventListener('click',e=>{if(e.target.closest('[data-close]')||e.target===$('dialog'))closeModal();});
  $('composer').addEventListener('submit',e=>{e.preventDefault();send($('message-input').value);});
  $('message-input').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();send(e.target.value);}});
  $('open-sidebar').addEventListener('click',openSidebar);
  $('close-sidebar').addEventListener('click',closeSidebar);
  if('serviceWorker' in navigator && (location.protocol==='https:' || location.hostname==='localhost'||location.hostname==='127.0.0.1')){
    navigator.serviceWorker.register('/sw.js').catch(()=>{});
  }
  try{await refresh();}catch(e){toast(e.message,true);}
}
document.addEventListener('DOMContentLoaded',startup);
