'use strict';
const $ = (id) => document.getElementById(id);
const escapeHtml = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let selected = null, knownDevices = [], knownTasks = [], toastTimer;
const descriptions = {notify:'发送工作通知',system_info:'查看电脑状态',list_files:'查看授权文件夹',read_text:'读取文本文件',write_note:'写入工作便签'};
const statusName = {queued:'待领取',running:'执行中',completed:'已完成',failed:'执行失败',interrupted:'连接中断',cancelled:'已取消'};
function taskRow(t){
  const state = t.status || 'unknown';
  const control = state==='queued'? `<button data-task-cancel="${escapeHtml(t.id)}" class="secondary-action">取消任务</button>`
    : ['failed','interrupted','cancelled'].includes(state)? `<button data-task-retry="${escapeHtml(t.id)}" class="secondary-action">重新安排</button>` : '';
  const attempted = t.retry_of ? '<span class="task-retry-hint">重新派发</span>' : '';
  return `<article class="remote-task"><div class="task-head"><strong>${escapeHtml(descriptions[t.action] || t.action)} ${attempted}</strong>
     <span class="task-status ${escapeHtml(state)}">${escapeHtml(statusName[state] || state)}</span></div>
     <div class="task-meta">交代于 ${escapeHtml(new Date(t.created_at).toLocaleString('zh-CN'))}</div>
     ${state==='running'?'<p class="task-working">电脑正在处理，请保持电脑工作进程运行。</p>':''}
     ${t.result?`<pre>${escapeHtml(t.result)}</pre>`:''}<div class="task-actions">${control}</div></article>`;
}
function renderTasks(){
  const filter=$('task-filter').value;
  const rows=knownTasks.filter(t=>filter==='all'||
    (filter==='active'&&['queued','running'].includes(t.status)) ||
    (filter==='completed'&&t.status==='completed') ||
    (filter==='attention'&&['failed','interrupted','cancelled'].includes(t.status)));
  $('task-list').innerHTML=rows.length?rows.map(taskRow).join(''):'<p class="task-empty">当前筛选条件下没有任务。</p>';
}

function toast(text){$('toast').textContent=text;$('toast').classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').classList.remove('visible'),4000);}
async function api(path, method='GET', data) {
  const headers = {'Content-Type':'application/json'};
  let token=sessionStorage.getItem('agentTeamToken');
  if (!token) {token=prompt('请输入服务器所有者访问口令：')||'';if(token)sessionStorage.setItem('agentTeamToken',token);}
  if(token)headers['X-Agent-Team-Token']=token;
  let response;
  try{response=await fetch('/api'+path,{method,headers,body:data?JSON.stringify(data):undefined});}
  catch{throw Error('网络不可用，请检查服务器和 HTTPS/VPN 连接');}
  const obj=await response.json().catch(()=>({}));
  if(response.status===401){sessionStorage.removeItem('agentTeamToken');throw Error('访问口令错误，请刷新页面重试');}
  if(!response.ok)throw Error(obj.detail || `HTTP ${response.status}`);
  return obj;
}
function online(lastSeen){return lastSeen && (Date.now()-new Date(lastSeen).getTime())<30000;}
function renderDevices(){
  $('device-list').innerHTML=knownDevices.length? knownDevices.map(d=>`<div class="remote-device ${d.id===selected?'selected':''}">
    <div><strong>💻 ${escapeHtml(d.name)}</strong><small>${online(d.last_seen_at)?'🟢 在线':'⚪ 离线'} · ${escapeHtml(d.platform || '等待连接')} · 最近连接：${escapeHtml(d.last_seen_at?new Date(d.last_seen_at).toLocaleString('zh-CN'):'未连接')}</small></div>
    <div class="buttons"><button data-select="${escapeHtml(d.id)}">选择</button><button data-revoke="${escapeHtml(d.id)}">撤销</button></div></div>`).join(''):'<p>暂无设备。先配对你的 Windows / Linux 电脑。</p>';
  if(selected && !knownDevices.find(d=>d.id===selected))selected=null;
  $('task-panel').hidden=!selected;
  if(selected){$('selected-name').textContent=knownDevices.find(d=>d.id===selected)?.name||'';}
}
async function refresh(){
  try{knownDevices=(await api('/devices')).devices;renderDevices();if(selected)await refreshTasks();}
  catch(e){toast(e.message);}
}
async function refreshTasks(){
  if(!selected)return;
  const id=selected;
  const rows=(await api('/devices/'+encodeURIComponent(id)+'/tasks')).tasks;
  if(selected!==id)return;
  knownTasks=rows;
  renderTasks();
}
$('task-filter').addEventListener('change',renderTasks);
$('task-list').addEventListener('click',async(e)=>{
  const el=e.target.closest('button');if(!el||!selected)return;
  const taskId=el.dataset.taskCancel||el.dataset.taskRetry;
  if(!taskId)return;
  const isRetry=!!el.dataset.taskRetry;
  const task=knownTasks.find(t=>t.id===taskId);
  const warning=isRetry && task?.status==='interrupted'?'电脑可能已经执行了这项工作，但没能上传结果。请先核对电脑上是否已经完成，避免重复执行。确认重新安排？':
    isRetry?'确认把这项任务重新交给电脑？':'只允许取消尚未领取的任务。确认取消？';
  if(!confirm(warning))return;
  el.disabled=true;
  try{await api('/devices/'+encodeURIComponent(selected)+'/tasks/'+encodeURIComponent(taskId)+(isRetry?'/retry':'/cancel'),'POST');
    toast(isRetry?'已重新安排任务':'任务已取消');await refreshTasks();}
  catch(err){toast(err.message);el.disabled=false;}
});
$('device-list').addEventListener('click',async(e)=>{
  const el=e.target.closest('button');if(!el)return;
  if(el.dataset.select){selected=el.dataset.select;renderDevices();try{await refreshTasks();}catch(err){toast(err.message);}return;}
  if(el.dataset.revoke){const d=knownDevices.find(x=>x.id===el.dataset.revoke);if(!confirm(`撤销设备“${d?.name||'未知'}”的连接？`))return;
    try{await api('/devices/'+encodeURIComponent(el.dataset.revoke),'DELETE');if(selected===el.dataset.revoke)selected=null;await refresh();}catch(err){toast(err.message);}}
});
$('add-device').addEventListener('click',async()=>{
  const name=prompt('给电脑命名（例如：宿舍 Windows 主机）');if(!name?.trim())return;
  try{const d=await api('/devices','POST',{name:name.trim()});$('device-secret').value=d.device_token;$('pair-dialog').showModal();await refresh();}
  catch(e){toast(e.message);}
});
$('copy-secret').addEventListener('click',async()=>{try{await navigator.clipboard.writeText($('device-secret').value);toast('已复制设备密钥');}catch{ $('device-secret').select();toast('请长按复制密钥');}});
$('close-dialog').addEventListener('click',()=>{$('pair-dialog').close();$('device-secret').value='';});
$('action').addEventListener('change',()=>{const a=$('action').value;$('payload-label').hidden=a==='system_info';$('payload-label').firstChild.textContent=['list_files','read_text'].includes(a)?(a==='read_text'?'文件路径（相对工作目录）':'子目录（相对工作目录）'):'任务内容';$('payload').placeholder=a==='list_files'?'.':a==='read_text'?'例如：README.md':'发送到电脑的内容';});
$('send-task').addEventListener('click',async()=>{
  if(!selected)return;
  const action=$('action').value, text=$('payload').value.trim();
  const payload=action==='system_info'?{}:['list_files','read_text'].includes(action)?{path:text||(action==='list_files'?'.':'')}:{text};
  try{await api('/devices/'+encodeURIComponent(selected)+'/tasks','POST',{action,payload});toast('已加入队列，等待电脑领取');$('payload').value='';await refreshTasks();}
  catch(e){toast(e.message);}
});
$('payload').addEventListener('keydown',(e)=>{if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){e.preventDefault();$('send-task').click();}});
refresh();setInterval(()=>{if(!document.hidden)refresh();},6000);
