async function api(p,o){const r=await fetch(p,o);if(!r.ok)throw new Error(await r.text());return r.json();}
function addBot(){document.getElementById('bots').appendChild(botRow());}
function addProv(t){document.getElementById('provs').appendChild(provRow({type:t||'openrouter'}));}
function provData(row){return{type:row.querySelector('.ptype').value,api_key:row.querySelector('.pkey').value,model:row.querySelector('.pmodel').value,api_base:row.querySelector('.pbase').value};}
async function save(){const bots=Array.from(document.querySelectorAll('#bots .bot')).map(function(r){return{name:r.querySelector('.bname').value,token:r.querySelector('.btok').value};});
const provs=Array.from(document.querySelectorAll('#provs .prov')).map(function(r){const d=provData(r);return{name:r.querySelector('.pname').value,type:d.type,api_key:d.api_key,model:d.model,api_base:d.api_base};});
const gist_github_token = document.getElementById('gist_github_token').value;
const m=document.getElementById('saveMsg');m.textContent='saving...';
try{const saved=await api('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:Number(document.getElementById('user_id').value||0),debug:document.getElementById('debug').checked,bots:bots,providers:provs,gist_github_token:gist_github_token})});const s=saved.bot_sync||{};let msg='saved.';if((s.started||[]).length)msg+=' Bot started: '+s.started.join(', ')+'.';if((s.errors||[]).length)msg+=' Bot error: '+s.errors.join('; ');m.textContent=msg;setupMarkSaved();}catch(e){m.textContent='ERR '+e.message;}}
(async function(){try{const j=await api('/api/config');document.getElementById('user_id').value=j.user_id||'';
document.getElementById('debug').checked=!!j.debug;
if(j.gist_github_token) document.getElementById('gist_github_token').value=j.gist_github_token;
(j.bots||[]).forEach(function(b){document.getElementById('bots').appendChild(botRow(b));});
(j.providers||[]).forEach(function(p){document.getElementById('provs').appendChild(provRow(p));});
if(!(j.bots||[]).length)addBot();if(!(j.providers||[]).length)addProv('openrouter');}catch(e){addBot();addProv('openrouter');}setupMarkSaved();})();function botRow(b){b=b||{};const d=document.createElement('div');d.className='bot';
d.innerHTML='<div class=row><div><label>Bot name</label><input class=bname></div><div><label>Token</label><input class=btok type=password></div></div><button class=ghost onclick="testTg(this)"><i class="fa-solid fa-vial"></i> Test</button> <button class=ghost onclick="this.closest(\'.bot\').remove()"><i class="fa-solid fa-trash"></i> Delete</button> <span class=msgmuted></span>';d.querySelector('.bname').value=b.name||'';d.querySelector('.btok').value=b.token||'';return d;}
var PROV_DEFAULTS={'ollama':{base:'http://localhost:11434',model:'gemma3:4b',key:false},'ollama_cloud':{base:'https://api.ollama.com',model:'gemma3:4b',key:true},'llamacpp':{base:'http://localhost:8080/v1',model:'',key:false},'openrouter':{base:'https://openrouter.ai/api/v1',model:'',key:true},'deepseek':{base:'https://api.deepseek.com/v1',model:'deepseek-chat',key:true},'google':{base:'https://generativelanguage.googleapis.com/v1beta/openai',model:'gemini-2.5-flash',key:true},'nvidia':{base:'https://integrate.api.nvidia.com/v1',model:'nvidia/nemotron-3-super-120b-a12b',key:true}};
function applyProvType(row,force){const t=row.querySelector('.ptype').value;const d=PROV_DEFAULTS[t]||PROV_DEFAULTS['ollama'];
const base=row.querySelector('.pbase'),model=row.querySelector('.pmodel'),hint=row.querySelector('.keyhint');
if(force||!base.value)base.value=d.base;
if(!model.value&&d.model)model.value=d.model;
if(hint)hint.textContent=d.key?'(API key required)':'(no API key needed)';}
function provRow(p){p=p||{type:'ollama'};const d=document.createElement('div');d.className='prov';
d.innerHTML='<div class=row><div><label>Name</label><input class=pname></div><div><label>Type</label><select class=ptype onchange="applyProvType(this.closest(\'.prov\'),true)"><option value=openrouter>OpenRouter</option><option value=deepseek>DeepSeek</option><option value=ollama>Ollama (local)</option><option value=ollama_cloud>Ollama (online · ollama.com)</option><option value=llamacpp>llama.cpp</option><option value=google>Google</option><option value=nvidia>NVIDIA NIM</option></select></div></div><div class=row><div><label>API key <span class="keyhint muted"></span></label><input class=pkey type=password></div><div><label>API base</label><input class=pbase></div></div><div class=row><div><label>Model</label><input class=pmodel></div><div><label>Fetched</label><div class=modelline><select class=pmodellist><option value="">--</option></select><input class=pmodelfilter placeholder="filter..."></div></div></div><button class=ghost onclick="loadModels(this)"><i class="fa-solid fa-list"></i> Models</button> <button class=ghost onclick="testProv(this)"><i class="fa-solid fa-vial"></i> Test</button> <button class=ghost onclick="this.closest(\'.prov\').remove()"><i class="fa-solid fa-trash"></i> Delete</button> <span class=msg></span>';
d.querySelector('.pname').value=p.name||'';d.querySelector('.ptype').value=p.type||'openrouter';
d.querySelector('.pkey').value=p.api_key||'';d.querySelector('.pbase').value=p.base||'';d.querySelector('.pmodel').value=p.model||'';
d.querySelector('.pmodellist').onchange=function(){d.querySelector('.pmodel').value=this.value;};
const flt=d.querySelector('.pmodelfilter');if(flt)flt.oninput=function(){renderModels(d.querySelector('.pmodellist'));};
applyProvType(d,false);return d;}
async function testTg(btn){const row=btn.parentElement;const m=row.querySelector('.msgmuted');m.textContent='...';
try{const j=await api('/api/test/telegram',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:row.querySelector('.btok').value})});m.textContent='OK @'+j.username;}catch(e){m.textContent='ERR '+e.message;}}
async function testProv(btn){const row=btn.closest('.prov');const m=row.querySelector('.msg');m.textContent='...';
try{const j=await api('/api/test/provider',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(provData(row))});m.textContent='OK';fillModels(row,j.models||[]);}catch(e){m.textContent='ERR '+e.message;}}
function fillModels(row,models){const s=row.querySelector('.pmodellist');s._all=(models||[]).slice();renderModels(s);}
// renderModels rebuilds the Fetched select from the last fetched list, keeps the
// current pick when it survives, and applies the filter box sitting next to it.
function renderModels(s){const box=s.closest('.modelline'),flt=box?box.querySelector('.pmodelfilter'):null;
const q=(flt&&flt.value?flt.value:'').trim().toLowerCase(),all=s._all||[],cur=s.value;
s.innerHTML='<option value="">--</option>';
all.forEach(function(x){if(q&&String(x).toLowerCase().indexOf(q)<0)return;const o=document.createElement('option');o.value=x;o.textContent=x;s.appendChild(o);});
for(let i=0;i<s.options.length;i++){if(cur&&s.options[i].value===cur){s.value=cur;break;}}}
async function loadModels(btn){const row=btn.closest('.prov');const d=provData(row);
try{const j=await api('/api/models?'+new URLSearchParams({type:d.type,key:d.api_key,model:d.model,base:d.api_base}));fillModels(row,j.models||[]);}catch(e){alert(e.message);}};

// --- Unsaved-changes guard ---------------------------------------------------
// setupState() serializes exactly what save() posts, in the same order, so the
// page can tell whether the form still matches the last loaded or saved config.
// Comparing strings keeps the check cheap and, unlike a plain "dirty" flag, it
// stays quiet when a field is edited and then put back the way it was. The
// Fetched select and its filter box are deliberately absent: they only feed the
// Model input, which is compared instead.
var setupSnapshot = null;
function setupState() {
  var bots = Array.from(document.querySelectorAll('#bots .bot')).map(function (r) {
    return [r.querySelector('.bname').value, r.querySelector('.btok').value];
  });
  var provs = Array.from(document.querySelectorAll('#provs .prov')).map(function (r) {
    return [r.querySelector('.pname').value, r.querySelector('.ptype').value, r.querySelector('.pkey').value, r.querySelector('.pmodel').value, r.querySelector('.pbase').value];
  });
  return JSON.stringify({
    user_id: document.getElementById('user_id').value,
    debug: document.getElementById('debug').checked,
    gist_github_token: document.getElementById('gist_github_token').value,
    bots: bots, providers: provs,
  });
}
// Called once the initial config has been rendered and again after a successful
// save, so the baseline only ever holds values that are really on disk.
function setupMarkSaved() { setupSnapshot = setupState(); }
function setupDirty() { return setupSnapshot !== null && setupState() !== setupSnapshot; }
// Chrome and Firefox only show this dialog after the user has interacted with
// the page, which is exactly the case once a field has been touched. The Home
// link, a reload and closing the tab all go through here.
window.addEventListener('beforeunload', function (e) {
  if (!setupDirty()) return;
  e.preventDefault();
  e.returnValue = 'You have unsaved changes. Leave without saving?';
  return e.returnValue;
});
