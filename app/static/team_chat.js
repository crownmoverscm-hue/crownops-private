/* Stage 1: private staff chat. No client Inbox, provider or AI writes. */
(()=>{
 let drawer=null,selected='',cursor=0,retry=null,busy=false;
 const esc=v=>h(String(v??''));
 async function refresh(){
  if(!S.user){if(drawer){drawer.close();drawer.remove();drawer=null;}selected='';cursor=0;retry=null;document.querySelector('#team-chat-open')?.remove();return;}if(document.hidden)return;
  const top=document.querySelector('.topbar');if(!top)return;
  try{const access=await api('/team-chat/access');if(!access.enabled){document.querySelector('#team-chat-open')?.remove();if(drawer?.open)drawer.close();return;}}catch{return;}
  let button=document.querySelector('#team-chat-open');
  if(!button){button=document.createElement('button');button.id='team-chat-open';button.className='btn-soft';button.onclick=open;top.append(button);}
  try{
   const threads=await api('/team-chat/threads');const count=threads.reduce((n,t)=>n+t.unread,0);
   button.textContent='Chat'+(count?' · '+count:'');button.setAttribute('aria-label','Chat, '+count+' unread messages');
   if(!drawer?.open)return;
   const list=drawer.querySelector('#tc-list');list.innerHTML=threads.map(t=>`<button data-thread="${esc(t.id)}" aria-pressed="${selected===t.id}">${esc(t.title)} ${t.unread?`<b>${t.unread}</b>`:''}</button>`).join('')||'<p>No conversations yet.</p>';
   if(selected&&!busy){const messages=await api('/team-chat/threads/'+encodeURIComponent(selected)+'/messages');const panel=drawer.querySelector('#tc-messages');const bottom=panel.scrollHeight-panel.scrollTop-panel.clientHeight<50;
    const current=messages.length?messages[messages.length-1].seq:0;
    if(current!==cursor){panel.innerHTML=messages.map(m=>`<article><strong>${esc(m.sender)}</strong><time>${esc(new Date(m.created_at).toLocaleString())}</time><p>${esc(m.body)}</p></article>`).join('');cursor=current;if(bottom)panel.scrollTop=panel.scrollHeight;}
    if(document.hasFocus()&&current)await api('/team-chat/threads/'+encodeURIComponent(selected)+'/read',{method:'POST',data:{seq:current}});
   }
  }catch(e){if(drawer?.open)drawer.querySelector('#tc-status').textContent='Chat could not refresh. Your typed message is kept.';}
 }
 async function open(){
  if(drawer){drawer.showModal();await refresh();return;}
  drawer=document.createElement('dialog');drawer.id='team-chat';drawer.setAttribute('aria-label','Chat');
  drawer.innerHTML='<header><h2>Chat</h2><button id="tc-close" aria-label="Close chat">Close</button></header><nav aria-label="Chat views"><button data-view="team" aria-pressed="true">Team</button><button data-view="clients">Clients</button><button data-view="ai">AI Approvals</button></nav><p id="tc-status" role="status"></p><section id="tc-team"><button id="tc-new">New conversation</button><div id="tc-create" hidden></div><div id="tc-list"></div><section id="tc-messages" aria-label="Message history"></section><form id="tc-compose"><label>Message<textarea name="body" maxlength="8000" required></textarea></label><button type="submit">Send</button></form></section><p id="tc-later" hidden></p>';
  document.body.append(drawer);drawer.showModal();
  drawer.querySelector('#tc-close').onclick=()=>drawer.close();
  drawer.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>{drawer.querySelectorAll('[data-view]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));drawer.querySelector('#tc-team').hidden=b.dataset.view!=='team';const p=drawer.querySelector('#tc-later');p.hidden=b.dataset.view==='team';p.textContent=b.dataset.view==='clients'?'Client messaging remains in the existing Inbox and secure portal. Chat integration is not enabled yet.':'AI booking actions are not enabled. Existing human approval rules remain in force.';});
  drawer.querySelector('#tc-list').onclick=async e=>{const b=e.target.closest('[data-thread]');if(!b||busy)return;if(drawer.querySelector('textarea').value&&!confirm('Switch conversation and discard this unsent message?'))return;selected=b.dataset.thread;cursor=-1;retry=null;drawer.querySelector('textarea').value='';await refresh();};
  drawer.querySelector('#tc-new').onclick=async()=>{try{const people=await api('/team-chat/people');const box=drawer.querySelector('#tc-create');box.hidden=false;box.innerHTML='<label>Search staff<input id="tc-search" type="search"></label><label>Group name (optional for a DM)<input id="tc-title" maxlength="100"></label><div id="tc-people">'+people.map(p=>`<label data-name="${esc(p.name.toLowerCase())}"><input type="checkbox" value="${esc(p.id)}">${esc(p.name)} · ${esc(p.role)}</label>`).join('')+'</div><button id="tc-start">Start conversation</button>';box.querySelector('#tc-search').oninput=e=>box.querySelectorAll('[data-name]').forEach(p=>p.hidden=!p.dataset.name.includes(e.target.value.toLowerCase()));box.querySelector('#tc-start').onclick=async()=>{try{const t=await api('/team-chat/threads',{method:'POST',data:{title:box.querySelector('#tc-title').value,members:[...box.querySelectorAll('input:checked')].map(x=>x.value)}});selected=t.id;cursor=-1;box.hidden=true;await refresh();}catch(e){drawer.querySelector('#tc-status').textContent=e.message;}};}catch(e){drawer.querySelector('#tc-status').textContent=e.message;}};
  drawer.querySelector('#tc-compose').onsubmit=async e=>{e.preventDefault();if(busy)return;if(!selected){drawer.querySelector('#tc-status').textContent='Choose a conversation first.';return;}const field=drawer.querySelector('textarea'),body=field.value;if(!retry||retry.body!==body||retry.thread!==selected)retry={body,thread:selected,key:crypto.randomUUID()};busy=true;try{await api('/team-chat/threads/'+encodeURIComponent(selected)+'/messages',{method:'POST',data:{body,client_key:retry.key}});field.value='';retry=null;drawer.querySelector('#tc-status').textContent='Message sent.';}catch(e){drawer.querySelector('#tc-status').textContent='Send not confirmed. Retry the unchanged message safely. '+e.message;}finally{busy=false;await refresh();}};
  await refresh();
 }
 setInterval(refresh,3000);document.addEventListener('visibilitychange',refresh);
})();
