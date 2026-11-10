'use strict';
let activeBatch=null, batchCatalog=null;
const pendingBatchCommands=new Map();
const batchDate=x=>time(x?new Date(x*1000).toISOString():null);
function batchUsage(u){if(u?.estimated_usd==null)return esc(u?.state||'Not observed');return `Estimated / reserved $${Number(u.estimated_usd).toFixed(4)} · ${esc(u.requests)} requests · ${esc(u.reported_tokens)} reported tokens · ${esc(u.reserved_or_used_tokens)} tokens used or reserved · ${esc(u.unsettled_requests)} requests with uncertain usage`}
const usageCount=value=>Number.isSafeInteger(value)&&value>=0?value.toLocaleString():'Unavailable';
const usageCost=value=>typeof value==='number'&&Number.isFinite(value)&&value>=0?`$${value.toFixed(4)}`:'Unavailable';
function gameUsageSummary(u){
 if(!u||u.state==='Unavailable')return esc(u?.reason||'Per-game usage was not recorded');
 return `${usageCount(u.reported_tokens)} reported tokens · ${usageCost(u.estimated_usd)} estimated / reserved · ${usageCount(u.requests)} request admissions · ${usageCount(u.unsettled_requests)} unsettled reservations`;
}
function gameUsagePanel(context){
 const u=context?.game_usage,available=u&&u.state!=='Unavailable';
 const rows=available?`${kv('Reported total tokens',usageCount(u.reported_tokens))}${kv('Input / output tokens',`${usageCount(u.input_tokens)} / ${usageCount(u.output_tokens)}`)}${kv('Estimated / reserved cost',usageCost(u.estimated_usd))}${kv('Provider request admissions',usageCount(u.requests))}${kv('Tokens used or reserved',usageCount(u.reserved_or_used_tokens))}${kv('Unsettled request / token reservations',`${usageCount(u.unsettled_requests)} / ${usageCount(u.unsettled_token_requests)}`)}${kv('Usage observation',u.observed_unix?batchDate(u.observed_unix):'Saved attempt snapshot')}`:'';
 const note=available?'Totals apply to this game only. Provider-reported tokens exclude unknown usage. Estimated cost includes outstanding reservations and is not the billed amount.':u?.reason||'No managed-batch usage was saved for this capture.';
 return `<section class="panel" id="game-usage"><div class="panel-head"><span class="panel-title">Tokens & cost · this game</span>${badge(available?(['Starting','Running','Stopping'].includes(context.state)?'Live snapshot':'Saved snapshot'):'Unavailable','neutral')}</div>${rows?`<div class="kv">${rows}</div>`:''}<div class="section-intro">${esc(note)}${u?.state==='Partial'?`<br>${esc(u.reason)}`:''}${available?`<br>${esc(u.attribution)}`:''}${context?.batch_id?`<br><a href="${BASE}/batches/${encodeURIComponent(context.batch_id)}">Open batch and attempt history ↗</a>`:''}</div></section>`;
}
function batchProgress(p){return p?`${esc(p.state)} · ${esc(p.decisions)} decisions · ${batchDate(p.observed_unix)}`:'Not observed'}
async function batchWrite(path,data){
 const signature=JSON.stringify([path,data]);
 if(!pendingBatchCommands.has(signature))pendingBatchCommands.set(signature,crypto.randomUUID());
 const result=await api(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf,'Idempotency-Key':pendingBatchCommands.get(signature)},body:JSON.stringify(data)});
 pendingBatchCommands.delete(signature);return result;
}
async function batchesView(){
 const tail=location.pathname.split('/').filter(Boolean).pop();
 if(tail==='new'){if($('#batch-form'))return;batchCatalog=await api('batches/catalog');batchForm();return}
 if(tail.startsWith('batch-')){activeBatch=await api('batches/'+tail);batchDetail(activeBatch);return}
 const data=await api('batches?offset='+(new URLSearchParams(location.search).get('offset')||'0'));activeBatch=null;
 const health=data.health;
 app.innerHTML=heading('AUTOMATED PLAY','Batches','Fixed four-seat Commander pods · one isolated game at a time',`<a class="btn primary" href="${BASE}/batches/new">＋ New batch</a>`)+
 `<div class="section-note">${badge(health.supervisor)} Last supervisor observation: ${batchDate(health.observed_unix)}<br>${esc(health.scope)}. Queued work never resumes automatically after a supervisor restart.</div>`+
 `<section class="panel"><div class="table-scroll"><table><thead><tr><th>BATCH / CREATED</th><th>STATE</th><th>PROGRESS</th><th>LINEUP · SEAT ORDER</th><th>EXECUTION</th></tr></thead><tbody>${data.batches.map(b=>`<tr data-href="${BASE}/batches/${b.id}" tabindex="0" role="link"><td><strong>${esc(b.plan.name)}</strong><span class="sub">${batchDate(b.created_unix)}</span></td><td>${badge(b.state)}<span class="sub">${esc(b.message)}</span></td><td>${b.counts.Completed} native completed / ${b.plan.game_count} planned<span class="sub">${b.attempts.length} attempted · ${b.counts.unstarted} unstarted · ${b.counts.Failed} failed · ${b.counts.Interrupted} interrupted</span></td><td>${esc(b.plan.seats.map(s=>s.deck.label).join(' / '))}</td><td>${badge(b.plan.execution_mode==='fixture'?'Synthetic fixture':'Paid provider','neutral')}</td></tr>`).join('')||'<tr><td colspan="5"><div class="empty">No batches yet. Create a draft to review its lineup and limits.</div></td></tr>'}</tbody></table></div><div class="pagination">${data.total} batches${data.total>30?`<a class="btn" href="${BASE}/batches?offset=${Number(new URLSearchParams(location.search).get('offset')||0)+30}">Next page →</a><a href="${BASE}/batches">First page</a>`:''}</div></section>`;
}
function batchForm(){
 const c=batchCatalog;
 app.innerHTML=`<a class="back" href="${BASE}/batches">← Batches</a>`+heading('PLAN AUTOMATED PLAY','New batch','Save a draft, review the frozen plan, then explicitly Start.')+
 `<section class="panel"><form id="batch-form" class="review-form"><label>Name<input name="name" required maxlength="100" placeholder="Commander pod · investigation"></label><label>Purpose<textarea name="purpose" maxlength="1000" placeholder="What should these games help investigate?"></textarea></label><div class="batch-fields">${[0,1,2,3].map((_,i)=>`<label>Seat ${i+1}<select name="binding${i}">${c.catalog.map((p,n)=>`<option value="${esc(p.id)}" ${n===i?'selected':''}>${esc(p.deck.label)} · ${esc(p.displayName)}</option>`).join('')}</select></label>`).join('')}</div><div class="batch-fields">${[['game_count','Games planned',1,1,50],['wall_seconds','Wall seconds per game',1800,30,7200],['max_decisions','Decisions per game',10000,1,100000],['budget_usd','Batch estimated USD fence',1,.01,5],['max_requests','Batch provider request limit',100,1,10000],['max_tokens','Batch total token fence',1000000,10000,100000000]].map(([name,label,value,min,max])=>`<label>${label}<input type="number" name="${name}" value="${value}" min="${min}" max="${max}" step="${name==='budget_usd'?'.01':'1'}" required></label>`).join('')}<label>On failed/interrupted game<select name="failure_policy"><option value="pause">Pause for inspection</option><option value="continue">Continue after confirmed runtime cleanup</option></select></label><label>Execution<select name="execution_mode"><option value="paid">Paid · existing Tolaria key</option><option value="fixture">Synthetic legal fixture · no provider calls</option></select></label></div><p>Concurrency: 1. Seed: ${esc(c.native_seed)}. The native source records player turns; no conversion to rounds.</p><p>${esc(c.cost_note)}. Requests with unknown usage retain their reservations. A limit stops the isolated game and preserves its evidence; it does not manufacture a game result.</p><p>Engine ${mono(c.pins.engine)}<br>Gym ${mono(c.pins.gym)}</p><button class="btn primary" type="submit">Save draft for review</button><div id="batch-feedback" role="status"></div></form></section>`;
}
function batchDetail(b){
 const p=b.plan;const live=b.attempts.find(a=>['Starting','Running','Stopping'].includes(a.state));
 const controls=[];
 if(b.state==='Draft')controls.push(['start','Start reviewed batch']);
 if(['Queued','Running'].includes(b.state))controls.push(['pause','Pause after current']);
 if(b.state==='Paused'&&b.counts.unstarted>0)controls.push(['resume','Resume unstarted games']);
 if(!['Completed','Failed','Stopped','Needs reconciliation'].includes(b.state))controls.push(['finish','Finish current and stop']);
 if(['Running','Draining'].includes(b.state))controls.push(['cancel','Interrupt current game']);
 controls.push(['clone','Clone to new draft']);
 app.innerHTML=`<a class="back" href="${BASE}/batches">← Batches</a>`+heading('BATCH CONTROL',esc(p.name),esc(p.purpose||'No purpose supplied'),`<button class="btn" data-batch-copy>⧉ Copy batch brief</button>`)+
 `<section class="panel"><div class="panel-head"><span>${badge(b.state)} ${badge(p.execution_mode==='fixture'?'Synthetic fixture · no provider calls':'Paid · existing key','neutral')}</span><span class="count">revision ${b.version}</span></div><div class="section-intro">${esc(b.message)}</div><div class="kv">${kv('Planned / attempted',`${p.game_count} / ${b.attempts.length}`)}${kv('Native completed',b.counts.Completed)}${kv('Failed / interrupted',`${b.counts.Failed} / ${b.counts.Interrupted}`)}${kv('Unstarted',b.counts.unstarted)}${kv('Current native game',mono(live?.game_id||'Not observed'))}${kv('Current observation',live?.observation?`${batchDate(live.observation.observed_unix)} · ${Date.now()/1000-live.observation.observed_unix>20?'Stale observation':'Private native source'}<pre>${esc(JSON.stringify(live.observation.live_games,null,2))}</pre>`:'Unavailable or no active attempt')}</div><div class="batch-actions">${controls.map(([op,label])=>`<button class="btn ${op==='start'?'primary':''}" data-batch-command="${op}">${label}</button>`).join('')}</div><p class="section-intro">Pause and finish allow the current game to finish. Interrupt stops only this batch-owned isolated runtime and leaves its native outcome unknown unless a native terminal result was already recorded.</p><div id="batch-feedback" role="status"></div></section>`+
 `<section class="panel"><div class="panel-head"><span class="panel-title">Frozen plan · review before Start</span></div><div class="batch-plan"><ol>${p.seats.map(s=>`<li><strong>${esc(s.deck.label)}</strong> · ${esc(s.displayName)}<br>${mono(s.id)}<br>Binding fingerprint ${mono(p.pins.bindings?.[s.id])}</li>`).join('')}</ol><div class="kv">${kv('Per game limits',`${p.wall_seconds}s wall time · ${p.max_decisions} decisions`)}${kv('Whole batch fences',`$${p.budget_usd} estimate · ${p.max_requests} requests · ${p.max_tokens.toLocaleString()} tokens`)}${kv('Failure policy',esc(p.failure_policy))}${kv('Concurrency / seed',`1 · ${esc(p.seed_policy)}`)}${kv('Engine',mono(p.pins.engine))}${kv('Gym',mono(p.pins.gym))}${kv('Plan fingerprint',mono(b.fingerprint))}</div><p class="section-intro">Cost fences use conservative estimates and reservations, not guaranteed billing amounts. Recording completeness, native outcome, replay qualification and review status remain independent.</p></div></section>`+
 `<section class="panel"><div class="panel-head"><span class="panel-title">Attempts · retained independently</span></div>${b.attempts.map(a=>`<div class="triage-card"><div><h3>#${a.ordinal} ${badge(a.state)}</h3><p>${esc(a.message)}</p><div class="meta">${mono(a.id)} ${batchDate(a.created_unix)}</div><p>Native game ${mono(a.game_id)} · lobby ${mono(a.lobby_id)}</p><p>Winner: ${esc(a.is_draw===true?'Recorded draw':a.winner_id||'Unknown')} · recording: ${esc(a.recording_state)} · complete: ${a.recording_complete??'Unknown'}</p><p>This game: ${gameUsageSummary(a.game_usage)}</p><details><summary>Batch cumulative usage at this observation</summary><p>${batchUsage(a.budget)}</p></details><p>Provider: ${batchProgress(a.provider_progress)}</p>${a.game_id?`<a class="btn" href="${BASE}/history?q=${encodeURIComponent(a.game_id)}">Inspect game evidence ↗</a>`:''}${['Failed','Interrupted'].includes(a.state)?` <button class="btn" data-batch-command="retry" data-attempt="${a.id}">Create linked retry draft</button>`:''}</div></div>`).join('')||'<div class="empty">No attempts. Saving and reviewing this draft has not started a game.</div>'}</section>`+
 `<section class="panel"><div class="panel-head"><span class="panel-title">Command history</span></div><div class="section-intro">${b.events.map(e=>`<p>${esc(e.kind)} · ${esc(e.actor)} · ${batchDate(e.observed_unix)}</p>`).join('')}</div></section>`;
}
document.addEventListener('submit',async event=>{
 if(event.target.id!=='batch-form')return;event.preventDefault();const form=event.target;const button=form.querySelector('button[type=submit]');button.disabled=true;
 const values=new FormData(form),plan=Object.fromEntries(values);plan.bindings=[0,1,2,3].map(i=>values.get('binding'+i));for(let i=0;i<4;i++)delete plan['binding'+i];
 for(const k of ['game_count','wall_seconds','max_decisions','budget_usd','max_requests','max_tokens'])plan[k]=Number(plan[k]);
 try{const result=await batchWrite('batches',{plan});dirty=false;navigate(BASE+'/batches/'+result.batch.id)}catch(error){$('#batch-feedback').textContent=error.message}finally{button.disabled=false}
});
document.addEventListener('input',e=>{if(e.target.closest('#batch-form'))dirty=true});
document.addEventListener('click',async event=>{
 const button=event.target.closest('[data-batch-command]');
 if(button){const op=button.dataset.batchCommand;
 if(op==='cancel'&&!confirm('Interrupt this isolated game? Its capture is retained and its outcome remains unknown without a native terminal result.'))return;
 if(op==='start'&&!confirm('Start the reviewed four-seat batch with these frozen limits and execution mode?'))return;
 const data={version:activeBatch.version};if(op==='start')data.fingerprint=activeBatch.fingerprint;if(op==='retry')data.attempt_id=button.dataset.attempt;
 button.disabled=true;
 try{const result=await batchWrite('batches/'+activeBatch.id+'/'+op,data);toast('Batch command acknowledged');if(result.batch.id!==activeBatch.id)navigate(BASE+'/batches/'+result.batch.id);else await batchesView()}catch(error){$('#batch-feedback').textContent=error.message}finally{button.disabled=false}
 }
 if(event.target.closest('[data-batch-copy]')&&activeBatch){const b=activeBatch;await navigator.clipboard.writeText('Commander Gym batch investigation\n'+location.href+'\n'+JSON.stringify(b,null,2));toast('Batch brief copied')}
});
