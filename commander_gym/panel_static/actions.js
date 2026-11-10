'use strict';
let actionFeed={ref:null,generation:'',mode:'live',paused:false,busy:false,rows:[],caughtUp:false,controller:null};
function actionPanel(ref){
 actionFeed.controller?.abort();
 actionFeed={ref,generation:'',mode:'live',paused:false,busy:false,rows:[],caughtUp:false,controller:null};
 return `<section class="panel" id="action-feed"><div class="panel-head"><span class="panel-title">Game actions</span><span class="badge neutral" id="action-state">Reading recorded evidence</span></div><div class="section-intro">Readable native actions and events. Pilot proposals are separate from executed moves. Private administrative view.</div><div class="action-controls"><button class="btn" data-feed="latest">Latest</button><button class="btn" data-feed="first">From start</button><button class="btn" data-feed="pause">Pause updates</button><label><input type="checkbox" id="action-routine"> Include passes & phase changes</label><label>Turn <input type="number" id="action-turn" min="0" placeholder="All" aria-label="Filter actions by recorded turn"></label><button class="btn" data-feed="filter">Apply</button><a class="btn" id="download-record" href="${BASE}/api/games/${encodeURIComponent(ref)}/record" download="game-${encodeURIComponent(ref)}.txt">Download readable record</a></div><div id="action-rows"><div class="empty">Reading a bounded journal prefix…</div></div><div class="evidence-foot" id="action-coverage"></div><div class="action-controls"><button class="btn" data-feed="older">← Older</button><button class="btn" data-feed="newer">Newer →</button></div></section>`;
}
async function loadActions(mode=actionFeed.mode){
 const state=actionFeed,section=$('#action-feed');
 if(!section||!state.ref||state.busy||document.hidden)return;
 state.busy=true;
 state.controller=new AbortController();
 const timeout=setTimeout(()=>state.controller.abort(),8000);
 const q=new URLSearchParams({generation:state.generation,routine:$('#action-routine').checked?'1':'0'});
 const turn=$('#action-turn').value;if(turn!=='')q.set('turn',turn);
 if(mode==='first')q.set('first','1');
 if(mode==='older'&&state.rows.length)q.set('before',state.rows[0].position);
 if(mode==='newer'&&state.rows.length)q.set('after',state.rows.at(-1).position);
 try{
  const data=await api('games/'+encodeURIComponent(state.ref)+'/actions?'+q,{signal:state.controller.signal});
  if(actionFeed!==state||!$('#action-feed'))return;
  if(!data.actions.length&&['older','newer'].includes(mode)&&!data.reset){toast('No more matching actions in the indexed prefix.');return;}
  state.rows=data.actions;state.generation=data.generation;state.caughtUp=data.caught_up;
  failures=0;
  state.mode=mode==='live'?'live':'page';
  $('#action-state').textContent=state.paused?'Updates paused':!data.caught_up?'Partial prefix · catching up':state.mode==='live'?'Following latest recorded actions':'Reading history';
  $('#action-rows').innerHTML=data.actions.length?data.actions.map(e=>`<div class="action-entry ${e.kind==='error'?'action-error':''}" id="action-${esc(e.position)}"><div class="action-stamp">${e.turn==null?'Turn unknown':'Turn '+esc(e.turn)}${e.phase?' · '+esc(e.phase):''}<br>${time(e.recorded_at)}</div><div><p>${esc(e.text)}</p><a href="#event-${esc(e.sequence)}" data-action-evidence="${esc(e.sequence)}">Evidence #${esc(e.sequence)}</a> <span class="muted tiny">${esc(e.kind)}</span></div></div>`).join(''):'<div class="empty">No matching readable actions in the indexed prefix. Native actions may not have been recorded yet.</div>';
  $('#action-coverage').textContent=(data.reset?'Source changed; this page was reset. ':'')+(data.diagnostic|| (data.caught_up?'Caught up to the observed journal prefix.':'Partial record: indexing continues in bounded steps.'))+` ${data.indexed_actions.toLocaleString()} readable entries from ${data.source_rows.toLocaleString()} journal rows. Latest read ${new Date(data.observed_unix*1000).toLocaleTimeString()}. `+data.coverage;
  $('#download-record').textContent=data.caught_up?'Download readable record':'Download partial record';
  if(data.caught_up&&data.indexed_actions>2000)$('#download-record').textContent='Download record · first 2,000 entries';
  if(!data.caught_up&&!data.diagnostic&&state.mode==='live'&&!state.paused){setTimeout(()=>{if(actionFeed===state&&!state.paused&&state.mode==='live')loadActions('live')},300);}
 }catch(error){if(actionFeed===state&&$('#action-coverage')){$('#action-state').textContent='Read unavailable · last actions retained';$('#action-coverage').textContent=error.name==='AbortError'?'Reading timed out; retrying on the next update.':error.message;failures++;}}
 finally{clearTimeout(timeout);state.busy=false;}
}
async function pollActions(){
 if(!$('#action-feed')||actionFeed.paused||actionFeed.mode!=='live'||document.activeElement?.matches('.action-controls input,.action-controls select'))return;
 // Update only the feed; review drafts, selected evidence and scroll stay intact.
 const position=window.scrollY;await loadActions('live');window.scrollTo(0,position);
}
document.addEventListener('click',async event=>{
 const download=event.target.closest('#download-record');
 if(download){
  event.preventDefault();if(download.dataset.busy)return;download.dataset.busy='1';
  try{const response=await fetch(download.href);if(!response.ok)throw Error('Readable record unavailable; retry after indexing catches up.');const blob=await response.blob();if(blob.size>2*1024*1024)throw Error('Readable record exceeds the download limit.');const link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download=download.download;link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);toast('Readable game record downloaded');}catch(error){toast(error.message);}finally{delete download.dataset.busy;}
  return;
 }
 const evidence=event.target.closest('[data-action-evidence]');
 if(evidence){event.preventDefault();const sequence=evidence.dataset.actionEvidence;await loadEvidence('',sequence);$('#review-anchor').value='event:'+sequence;dirty=true;history.replaceState({},'',location.pathname+location.search+'#event-'+sequence);$('#evidence').scrollIntoView({block:'start'});return;}
 const button=event.target.closest('[data-feed]');if(!button)return;
 const op=button.dataset.feed;
 if(op==='pause'){actionFeed.paused=!actionFeed.paused;button.textContent=actionFeed.paused?'Resume updates':'Pause updates';$('#action-state').textContent=actionFeed.paused?'Updates paused':'Following latest recorded actions';if(!actionFeed.paused)await loadActions('live');return;}
 await loadActions(op==='latest'||op==='filter'?'live':op);
});
