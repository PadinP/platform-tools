// RFAF WebADB: migración del recorrido V13. No contiene credenciales.
// Persistencia vía componente Streamlit -> servidor Python -> Supabase.
import {Adb, AdbDaemonTransport} from 'https://esm.sh/@yume-chan/adb@0.0.20';
import AdbWebCredentialStore from 'https://esm.sh/@yume-chan/adb-credential-web@0.0.20';
import {AdbDaemonWebUsbDeviceManager} from 'https://esm.sh/@yume-chan/adb-daemon-webusb@0.0.20';

const $ = id => document.getElementById(id);
const btn = $('action'), credentials = new AdbWebCredentialStore();
const PACKAGE = 'com.novanet.FutbolAragonAppPublica';
const TARGETS = [
 {category:'FÚTBOL NACIONAL', aliases:['TERCERA FEDERACIÓN','TERCERA DIVISIÓN'],groups:[null]},
 {category:'FÚTBOL NACIONAL', aliases:['LIGA NACIONAL JUVENIL','LIGA NACIONAL'],groups:[null]},
 {category:'FÚTBOL REGIONAL',aliases:['REGIONAL PREFERENTE'],groups:['1','2']},
 {category:'FÚTBOL JUVENIL',aliases:['JUVENIL PREFERENTE','PREFERENTE JUVENIL'],groups:['Grupo 1 - Zaragoza','Grupo 2 - Huesca','Grupo 4 - Teruel','Grupo 3-A - Provincia Zaragoza','Grupo 3-B - Provincia Zaragoza']},
 {category:'FÚTBOL JUVENIL',aliases:['1ª JUVENIL','PRIMERA JUVENIL'],groups:['Grupo 1 - Zaragoza','Grupo 2 - Zaragoza']},
 {category:'FÚTBOL BASE',aliases:['DIVISIÓN HONOR CADETE'],groups:[null]}
];
const SEASONS=['26-27','25-26','24-25'];
const SKIP=new Set(['SUSPENDIDO','APLAZADO','CANCELADO','ANULADO']);
const FIELDS={
 'Temporada':'temporada','Categoria_origen':'categoria_origen','Competicion_origen':'competicion_origen',
 'Grupo_origen':'grupo_origen','Equipo_origen':'equipo_origen','Jugador':'jugador','Año_nacimiento':'año_nacimiento',
 'Club_en_temporada':'club_en_temporada','Estado':'estado','Convocados':'convocados','Titular':'titular',
 'Suplente':'suplente','Jugados':'jugados','Total_Goles':'total_goles','Media_Goles':'media_goles','Amarillas':'amarillas',
 'Rojas':'rojas','Doble_Amarilla':'doble_amarilla','Competiciones_temporada':'competiciones_temporada'
};
let adb=null, running=false, cancelled=false, args={}, seq=0, pendingAck=null;
let usbDisconnected=false, activeCommandCancel=null, inFlightShell=null;
let resumeOnUsbReconnect=false, reconnectAttempting=false, confirmedCheckpoint=null, pausedByUser=false;
let usbReconnectPending=false, disconnectTimer=null, activeUsbIdentity=null, runGeneration=0, currentRunAutomatic=false;
let lastRunStoppedByUser=false, lastRecoveryFailureAt=0;
const clientSession=crypto.randomUUID();
let checkpoint={jugadores:[],equipos:[],partidos:[],grupos:[],anonymous_rows:{}};
let players=new Set(),teams=new Set(),matches=new Set(),groups=new Set(),anonymous={};
let progress={players:0,teams:0,matches:0};
const showProgress=(field,value)=>{const e=$(field);if(e)e.textContent=String(value)};
function showTeamCount(done,total){showProgress('progress-squad',`${done} / ${total}`);const pct=total?Math.min(100,Math.max(0,Math.round(100*done/total))):0;const fill=$('progress-fill');if(fill)fill.style.width=`${pct}%`;showProgress('progress-percent',`${pct}%`);const bar=$('progress-bar');if(bar)bar.setAttribute('aria-valuenow',String(pct))}
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const norm=v=>String(v??'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toUpperCase().replace(/[^A-Z0-9]/g,'');
const clean=v=>String(v??'').replace(/[\uE000-\uF8FF]/g,'').replace(/\s+/g,' ').trim();
const key=(...parts)=>parts.map(norm).join('|||');
const txt=n=>n?.getAttribute('text')||'';
const desc=n=>n?.getAttribute('content-desc')||'';
const val=n=>clean(desc(n)||txt(n));
const all=root=>root?Array.from(root.getElementsByTagName('node')):[];
const descendants=n=>n?[n,...all(n)]:[];
const byId=(root,id)=>all(root).filter(n=>n.getAttribute('resource-id')===id);
const first=(root,id)=>byId(root,id).find(n=>bounds(n))||null;
const bounds=node=>{const b=(node?.getAttribute('bounds')||'').match(/-?\d+/g)?.map(Number)||[];return b.length===4&&b[2]>b[0]&&b[3]>b[1]?b:null};
const center=node=>{const b=bounds(node);return b?[Math.round((b[0]+b[2])/2),Math.round((b[1]+b[3])/2)]:null};
const clickable=n=>{for(let el=n;el?.tagName==='node';el=el.parentElement)if(el.getAttribute('clickable')==='true'&&bounds(el))return el;return bounds(n)?n:null};
const textMatch=(node,wanted)=>norm(txt(node))===norm(wanted)||norm(desc(node))===norm(wanted);
const contained=(root,id,wanted)=>{const parent=first(root,id);if(!parent)return null;const item=descendants(parent).find(n=>textMatch(n,wanted));return item?clickable(item):null};
function status(s){
  const el=$('status');
  const v=String(s).toLocaleLowerCase('es');
  const overlay=$('usb-overlay');if(overlay)overlay.hidden=!/desconectado|reconexión pendiente|autorización usb requerida/i.test(s);
  const permissionButton=$('usb-authorize');if(permissionButton)permissionButton.hidden=!/autorización usb requerida/i.test(s);
  const cls=/desconectado|reconexión|reconectado|pausad|pendiente|pausando/.test(v)?'state-paused'
    : /error|no disponible/.test(v)?'state-error'
    : /finalizada|completada/.test(v)?'state-success'
    : /cancelado/.test(v)?'state-idle':'state-active';
  const label=cls==='state-active'?'Ejecutando':cls==='state-error'?'Error':cls==='state-success'?'Finalizado'
    : cls==='state-paused'?(/desconectado/.test(v)?'Móvil desconectado':/reconexión|reconectado/.test(v)?'Reconectando':'En pausa'):'Preparado';
  el.textContent=label;
  el.className=`pill ${cls}`;
  el.title=String(s);
}

const technicalHistory=[];
const HIDDEN_LOG=/^(?:Conexión ADB:|Checkpoint:|Persistencia Supabase|ATENCIÓN:|INICIANDO |Competición:|Jornada \d+|Partido:|PARTIDO:|La app indica|Revisión plantilla|USB reconectado|↻ Móvil reconectado|⚠ Móvil desconectado o comunicación|Se conserva el último checkpoint|Jornadas visibles:|Orden de selección enviada)/i;
function log(s){
  technicalHistory.push(`[${new Date().toLocaleTimeString()}] ${s}`);
  if(technicalHistory.length>600)technicalHistory.shift();
  if(HIDDEN_LOG.test(String(s).trim()))return;
  const box=$('log');if(!box)return;
  if(box.textContent==='Consola preparada.')box.textContent='';
  const line=`[${new Date().toLocaleTimeString()}] ${s}\n`;
  if(box.textContent.endsWith(line))return;
  box.textContent+=line;
  if(box.textContent.length>12000)box.textContent=box.textContent.slice(-9000);
  box.scrollTop=box.scrollHeight;
}
// Cola de diagnósticos: espera las confirmaciones de jugadores/checkpoints.
// Evita perder el aviso USB cuando el canal Streamlit esté ocupado.
const diagnosticQueue=[];
let flushingDiagnostics=false;
const importantDiagnosticEvents=new Set(['usb_disconnect','usb_reconnect','recovery_error','scraper_error','write_error','complete']);
function diagnostic(code,details=''){
  if(!args.write_enabled||!importantDiagnosticEvents.has(code))return Promise.resolve();
  const value=String(details).slice(0,450);
  if(diagnosticQueue.some(e=>e.code===code&&e.details===value))return Promise.resolve();
  if(diagnosticQueue.length>=30)diagnosticQueue.shift();
  diagnosticQueue.push({code,details:value});
  void flushDiagnostics();
  return Promise.resolve(); // Nunca bloquear la navegación por un error de telemetría.
}
async function flushDiagnostics(){
  if(flushingDiagnostics)return;
  flushingDiagnostics=true;
  try{
    while(diagnosticQueue.length){
      // Una operación de guardado debe completar primero; esperar como máximo 65 s.
      let wait=0;
      while(pendingAck&&wait<130){await sleep(500);wait++}
      if(pendingAck){technicalHistory.push('Diagnóstico pendiente: canal ocupado');break}
      const item=diagnosticQueue[0];
      try{
        await emit('diagnostic',item);
        diagnosticQueue.shift();
      }catch(err){
        technicalHistory.push('No se pudo registrar '+item.code+': '+String(err?.message||err));
        // Mantener el evento para un intento posterior sin bucle infinito.
        break;
      }
    }
  }finally{flushingDiagnostics=false;}
}

function draw(){btn.textContent=running?(pausedByUser?'⏳ Terminando comando…':'⏸ Pausar actualización'):(resumeOnUsbReconnect?'▶ Reanudar actualización':'▶ Actualizar jugadores');btn.disabled=running&&pausedByUser}
function guard(){if(usbDisconnected)throw new Error('USB_DISCONNECTED');if(cancelled||pausedByUser)throw new Error('STOP_REQUESTED')}
function decode(value){if(typeof value==='string')return value.trim();if(value instanceof Uint8Array||value instanceof ArrayBuffer)return new TextDecoder().decode(value).trim();return String(value??'').trim()}
// Todos los comandos tienen tiempo máximo y pueden cancelarse sin esperar al socket USB.
async function shell(command){
  guard();
  if(!adb)throw new Error('ADB desconectado');
  const timeoutMs=command.startsWith('uiautomator ') ? 18000 : 12000;
  let timer;
  let cancelThisCommand;
  const interrupted=new Promise((_,reject)=>{cancelThisCommand=reject;});
  activeCommandCancel=cancelThisCommand;
  const watchdog=new Promise((_,reject)=>{
    timer=setTimeout(()=>reject(new Error(`ADB_TIMEOUT: ${command.split(' ')[0]} tardó más de ${timeoutMs/1000} segundos`)),timeoutMs);
  });
  // Registrar el proceso real: Promise.race NO cancela spawnAndWait.
  // Nunca abrir una nueva conexión sobre una lectura anterior aún activa.
  const operation=adb.subprocess.spawnAndWait(command);
  inFlightShell=operation;
  void operation.catch(()=>{}); // absorbe un rechazo si vence el watchdog
  try{
    const r=await Promise.race([operation,interrupted,watchdog]);
    guard();
    if(r.exitCode!=null&&r.exitCode!==0)throw new Error(`ADB ${r.exitCode}: ${decode(r.stderr).slice(0,240)}`);
    return decode(r.stdout);
  }catch(err){
    const description=String(err?.message||err);
    if(description.includes('transferIn')||description.includes('buffer exceeded')){
      throw new Error(`WEBUSB_TRANSFER_ERROR [${command.slice(0,65)}]: ${description}`);
    }
    throw err;
  }finally{
    clearTimeout(timer);
    if(activeCommandCancel===cancelThisCommand)activeCommandCancel=null;
    // Sólo se borra cuando la operación realmente termina, no al vencer el timeout.
    if(inFlightShell===operation)void operation.finally(()=>{if(inFlightShell===operation)inFlightShell=null}).catch(()=>{});
  }
}
function interruptCommand(reason){
  if(activeCommandCancel){const cancel=activeCommandCancel;activeCommandCancel=null;cancel(new Error(reason));}
  if(pendingAck){const pending=pendingAck;pendingAck=null;clearTimeout(pending.timer);pending.reject(new Error(reason));}
  setTimeout(()=>{void flushDiagnostics()},0);
}
// No esperamos indefinidamente al cierre de una conexión dañada.
async function closeConnection(){
  const old=adb;
  adb=null;
  if(!old)return;
  // Un cierre de socket con lectura en curso puede dejar un USB transferIn pendiente.
  // Esperar brevemente y después cerrar la conexión dañada antes de permitir otro inicio.
  const inFlight=inFlightShell;
  if(inFlight)await Promise.race([inFlight.catch(()=>{}),sleep(2500)]);
  try{await Promise.race([Promise.resolve().then(()=>old.close?.()),sleep(2500)]);}catch(_){}
  if(inFlightShell)await Promise.race([inFlightShell.catch(()=>{}),sleep(1500)]);
}
// Sólo tratar una desconexión del teléfono que utiliza esta ejecución.
// Algunos equipos emiten eventos USB transitorios al cambiar de interfaz.
function sameActiveUsb(device){
  if(!activeUsbIdentity || !device)return false;
  if(device.vendorId!==activeUsbIdentity.vendorId || device.productId!==activeUsbIdentity.productId)return false;
  return !activeUsbIdentity.serialNumber || !device.serialNumber || device.serialNumber===activeUsbIdentity.serialNumber;
}
function cancelDisconnectCheck(){if(disconnectTimer!==null){clearTimeout(disconnectTimer);disconnectTimer=null;}}
navigator.usb?.addEventListener('disconnect',event=>{
  if(!running || pausedByUser || cancelled || !sameActiveUsb(event.device))return;
  cancelDisconnectCheck();
  const generation=runGeneration;
  disconnectTimer=setTimeout(async()=>{
    disconnectTimer=null;
    if(!running || pausedByUser || cancelled || generation!==runGeneration)return;
    // Si el teléfono sigue presente, era un evento transitorio y el scraping sigue.
    try{
      const devices=await navigator.usb.getDevices();
      if(devices.some(sameActiveUsb))return;
    }catch(_){}
    if(usbDisconnected)return;
    usbDisconnected=true;
    resumeOnUsbReconnect=true;
    status('Móvil desconectado · pausado');
    $('device').textContent='Conecta el móvil por USB y abre la app RFAF para continuar.';
    log('⚠ Móvil desconectado. Actualización pausada.');
    void diagnostic('usb_disconnect','USB desconectado durante la ejecución');
    interruptCommand('USB_DISCONNECTED');
  },4500);
});
async function tap(node,label='elemento'){const p=center(node);if(!p)throw new Error(`No puedo pulsar ${label}: sin límites`);await shell(`input tap ${p[0]} ${p[1]}`);await sleep(160)}
async function back(){await shell('input keyevent 4');await sleep(180)}
async function swipe(root,from=.78,to=.24,duration=240){const b=bounds(root);if(!b)throw new Error('Contenedor sin coordenadas visibles');const x=Math.round((b[0]+b[2])/2);await shell(`input swipe ${x} ${Math.round(b[1]+(b[3]-b[1])*from)} ${x} ${Math.round(b[1]+(b[3]-b[1])*to)} ${duration}`);await sleep(220)}
async function dump(){
  // UIAutomator abre un proceso nuevo por lectura. Un cierre prematuro del socket
  // puede ser transitorio; nunca se confirma ningún checkpoint desde aquí.
  let lastError=null;
  for(let attempt=1;attempt<=3;attempt++){
    guard();
    try{
      await shell('uiautomator dump /sdcard/rfaf_webadb.xml');
      const output=await shell('cat /sdcard/rfaf_webadb.xml');
      const start=output.indexOf('<?xml'),end=output.lastIndexOf('</hierarchy>');
      if(start<0||end<0)throw new Error('UIAutomator devolvió un XML incompleto');
      const doc=new DOMParser().parseFromString(output.slice(start,end+12),'application/xml');
      if(doc.querySelector('parsererror'))throw new Error('XML Android inválido');
      return doc;
    }catch(err){
      if(cancelled||usbDisconnected||err.message==='STOP_REQUESTED'||err.message==='USB_DISCONNECTED'||String(err.message).startsWith('ADB_TIMEOUT'))throw err;
      lastError=err;
      if(attempt<3){
        // Los reintentos internos no llenan la consola del usuario.
        await sleep(650*attempt);
      }
    }
  }
  throw new Error(`No se pudo leer la interfaz Android tras 3 intentos: ${lastError?.message||'sin detalle'}`);
}
function state(root){for(const [id,s] of [['player-screen','player'],['team-plantilla-modal','squad'],['team-screen','team'],['match-screen','match'],['competition-sections-sheet','sheet'],['competition-screen','competition']])if(byId(root,id).length)return s;return byId(root,'competition-category-item').length?'categories':'other'}
async function until(wanted,attempts=7){const states=Array.isArray(wanted)?wanted:[wanted];for(let i=0;i<attempts;i++){const root=await dump();if(states.includes(state(root)))return root;await sleep(240)}return null}
function initCheckpoint(data){checkpoint=structuredClone(data||{});confirmedCheckpoint=structuredClone(checkpoint);for(const name of ['jugadores','equipos','partidos','grupos'])if(!Array.isArray(checkpoint[name]))checkpoint[name]=[];if(!checkpoint.anonymous_rows||typeof checkpoint.anonymous_rows!=='object')checkpoint.anonymous_rows={};players=new Set(checkpoint.jugadores);teams=new Set(checkpoint.equipos);matches=new Set(checkpoint.partidos);groups=new Set(checkpoint.grupos);anonymous={...checkpoint.anonymous_rows};log(`Checkpoint: ${teams.size} equipos | ${players.size} jugadores | ${matches.size} partidos`)}
function snapshot(){return {jugadores:[...players].sort(),equipos:[...teams].sort(),partidos:[...matches].sort(),grupos:[...groups].sort(),anonymous_rows:anonymous}}
// Canal bidireccional del componente de Streamlit. No se envían credenciales al navegador.
function emit(type,payload){if(pendingAck)throw new Error('Ya hay una escritura pendiente');const id=++seq;return new Promise((resolve,reject)=>{const timer=setTimeout(()=>{pendingAck=null;reject(new Error('El servidor Streamlit no confirmó la escritura en 60 segundos'))},60000);pendingAck={id,resolve,reject,timer};window.parent.postMessage({isStreamlitMessage:true,type:'streamlit:setComponentValue',value:{session:clientSession,id,type,payload}},'*')})}
async function saveRow(row){if(!args.write_enabled){log(`VISTA PREVIA: ${row.jugador} · ${row.temporada} (escritura desactivada)`);return}await emit('player',row)}
async function saveCheckpoint(){if(!args.write_enabled)return;const next=snapshot();await emit('checkpoint',next);confirmedCheckpoint=structuredClone(next)}
function playerValid(v){const s=clean(v);return /[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]/.test(s)&&[...s].filter(c=>/\p{L}/u.test(c)).length>=4&&!/^\d{2}-\d{2}$/.test(s)&&!/^\d{4}$/.test(s)&&!['PARTIDOS','TARJETAS','COMPETICIONES','CONVOCADOS','TITULAR','SUPLENTE','JUGADOS','TOTALGOLES','MEDIAGOLES','AMARILLAS','ROJAS','DOBLEAMARILLA','POSICION','PUNTOS'].includes(norm(s))&&!norm(s).includes('SEGUIDOR')}
function pickOne(node,condition){return descendants(node).find(condition)||null}
async function goCategories(){for(let k=0;k<12;k++){const root=await dump(),s=state(root);if(s==='categories')return root;if(['player','squad','team','match','sheet','competition'].includes(s)){await back();continue}const tab=first(root,'tab-competitions-button');if(tab)await tap(tab,'Competiciones');else await back()}throw new Error('No pude llegar a Competiciones')}
async function openTarget(target,group){const root=await goCategories();const cat=byId(root,'competition-category-item').find(n=>descendants(n).some(k=>norm(txt(k)+' '+desc(k)).includes(norm(target.category))));if(!cat)throw new Error('Categoría no encontrada: '+target.category);await tap(cat,target.category);if(!await until('sheet'))throw new Error('No aparece lista de competiciones');let competition='';for(let i=0;i<12;i++){const r=await dump();let found=null;for(const alias of target.aliases){found=contained(r,'competition-sections-sheet',alias);if(found){competition=alias;break}}if(found){await tap(found,competition);break}await swipe(first(r,'competition-sections-sheet'))}if(!competition)throw new Error('Competición no encontrada: '+target.aliases.join('/'));let r=await until(['sheet','competition']);if(!r)throw new Error('Pantalla competición no se abrió');let realGroup=group||'';if(state(r)==='sheet'){const labels=group?[String(group),`GRUPO ${group}`]:['1','GRUPO 1','ÚNICO','UNICO'];let success=false;for(let i=0;i<10&&!success;i++){r=await dump();for(const label of labels){const found=contained(r,'competition-sections-sheet',label);if(found){await tap(found,`Grupo ${label}`);realGroup=group?String(group):label;success=true;break}}if(!success)await swipe(first(r,'competition-sections-sheet'))}if(!success||!await until('competition'))throw new Error('No se pudo abrir el grupo')}log(`Competición: ${target.category} | ${competition} | ${realGroup||'-'}`);return {competition,group:realGroup}}
async function calendarTop(){let unchanged=0,last='';for(let k=0;k<28;k++){const root=await dump(),tab=first(root,'competition-view-tab-0');if(tab&&bounds(tab)[1]<1100)return root;const list=first(root,'competition-calendar-list')||first(root,'competition-screen');if(!list)break;const sig=all(root).filter(n=>n.getAttribute('resource-id')==='competition-view-tab-0').map(n=>bounds(n)).join('');unchanged=sig===last?unchanged+1:0;last=sig;await swipe(list,.25,.86,310);if(unchanged>3){await shell('input keyevent 122');unchanged=0}}return dump()}
function dayControls(root){const allPrev=byId(root,'day-slider-prev-button').filter(bounds);const allNext=byId(root,'day-slider-next-button').filter(bounds);const choose=arr=>arr.filter(n=>bounds(n)[1]<1500).sort((a,b)=>bounds(b)[1]-bounds(a)[1])[0]||null;const prev=choose(allPrev),next=choose(allNext);const list=[prev,next].filter(Boolean).map(bounds);return {prev,next,top:list.length?Math.max(0,Math.min(...list.map(b=>b[1]))-180):450,bottom:list.length?Math.max(...list.map(b=>b[3]))+180:1200}}
async function selectDay(wanted){
  let root=await calendarTop();
  let tab=first(root,'competition-view-tab-0');
  if(tab){await tap(tab,'Calendario');root=await dump()}
  let stagnant=0,last='';
  for(let k=0;k<30;k++){
    guard();
    const {prev,next,top,bottom}=dayControls(root);
    const visible=[];
    const candidates=[];
    for(const n of all(root)){
      const b=bounds(n);
      if(!b)continue;
      const cy=(b[1]+b[3])/2;
      if(cy<top||cy>bottom)continue;
      const v=val(n);
      if(!/^\d+$/.test(v))continue;
      const num=Number(v);
      if(num<1||num>60)continue;
      visible.push(num);
      if(num===wanted)candidates.push(n);
    }
    const unique=[...new Set(visible)].sort((x,y)=>x-y);
    log(`Jornadas visibles: [${unique.join(', ')}] · buscando ${wanted}`);
    if(candidates.length){
      // En v13 la jornada sólo se considera seleccionada después de pulsar
      // un nodo clickable. La versión anterior devolvía true sin pulsarlo.
      let chosen=candidates.find(n=>n.getAttribute('clickable')==='true'&&n.getAttribute('enabled')!=='false');
      if(!chosen){
        // Algunos Android exponen el número como texto hijo de un botón.
        const parents=new Map();
        for(const n of all(root))for(const c of Array.from(n.children))parents.set(c,n);
        for(const n of candidates){
          let p=n;
          for(let i=0;i<8&&p;i++,p=parents.get(p)){
            const bb=bounds(p);
            if(bb&&p.getAttribute('clickable')==='true'&&p.getAttribute('enabled')!=='false'){
              chosen=p;break;
            }
          }
          if(chosen)break;
        }
      }
      if(chosen){
        await tap(chosen,`Seleccionando jornada ${wanted}`);
        await sleep(220);
        root=await dump();
        // Android puede no exponer un estado selected; verificamos al menos que
        // el selector sigue presente tras la acción, sin inventar confirmaciones.
        if(state(root)!=='competition'){
          throw new Error(`Después de seleccionar jornada ${wanted}, la pantalla de competición no está disponible`);
        }
        log(`Orden de selección enviada a jornada ${wanted}. Comprueba que los partidos corresponden a ella.`);
        return true;
      }
      throw new Error(`Jornada ${wanted} visible pero no hay elemento pulsable. No se procesarán partidos de otra jornada.`);
    }
    if(!unique.length){root=await calendarTop();continue;}
    const min=unique[0],max=unique.at(-1);
    if(wanted>max&&(!next||next.getAttribute('enabled')!=='true'))return 'END';
    const btn=wanted<min?prev:next;
    if(!btn||btn.getAttribute('enabled')!=='true')return wanted>max?'END':false;
    const sig=unique.join(',');
    stagnant=sig===last?stagnant+1:0;
    last=sig;
    if(stagnant>=3&&wanted>max)return 'END';
    await tap(btn,`Mover selector hacia jornada ${wanted}`);
    root=await dump();
  }
  return false;
}
function blockStatus(block){for(const n of descendants(block)){const s=norm(txt(n)+' '+desc(n));for(const st of ['SUSPENDIDO','APLAZADO','CANCELADO','ANULADO','FINALIZADO','PENDIENTE','ENJUEGO'])if(s.includes(st))return st}return ''}
function visibleMatches(root){const list=first(root,'competition-calendar-list');if(!list)return [];const items=[];for(const block of Array.from(list.children)){const s=blockStatus(block);const node=pickOne(block,n=>n.getAttribute('clickable')==='true'&&bounds(n));if(!node&&!SKIP.has(s))continue;let local='',away='',date='';const teams=[];for(const n of descendants(block)){const v=clean(txt(n)),b=bounds(n);if(!v||!b)continue;if(/^\d{2}\/\d{2}\/\d{4}$/.test(v)){date=v;continue}if(/^\d{1,2}:\d{2}$/.test(v)||/^\d+\s*-\s*\d+$/.test(v)||/^\d+$/.test(v)||['FINALIZADO','SUSPENDIDO','APLAZADO','PENDIENTE','ENJUEGO','CANCELADO','ANULADO'].includes(norm(v)))continue;if(b[0]>=180&&b[0]<=780&&(!teams.length||norm(teams.at(-1))!==norm(v)))teams.push(v)}if(teams.length>=2){[local,away]=teams;items.push({local,away,date,status:s,node})}}return items}
function matchSignature(root){return visibleMatches(root).map(m=>key(m.local,m.away,m.date)).join('~')}
async function scrollMatches(root){const list=first(root,'competition-calendar-list');if(!list)throw new Error('Lista de partidos no visible');await swipe(list,.84,.37,190)}
async function processMatchday(category,competition,group,day){const selected=await selectDay(day);if(selected!==true)return selected;showProgress('progress-day',day);log(`Jornada ${day}`);const seen=new Set();let stagnant=0;for(let scan=0;scan<55;scan++){let root=await dump();let candidate=null;for(const m of visibleMatches(root)){const k=key(m.local,m.away,m.date);if(!seen.has(k)){seen.add(k);candidate=m;break}}if(candidate){log(`Partido: ${candidate.local} vs ${candidate.away}${candidate.status?' ['+candidate.status+']':''}`);const result=await processMatch(category,competition,group,candidate);if(result===false)return false;continue}const before=matchSignature(root);await scrollMatches(root);root=await dump();const after=matchSignature(root);stagnant=before===after?stagnant+1:0;if(stagnant>=2)break}return true}
async function processMatch(category,competition,group,m){const mk=key(category,competition,group,m.local,m.away,m.date),lk=key(category,competition,group,m.local),ak=key(category,competition,group,m.away);if(matches.has(mk)||(teams.has(lk)&&teams.has(ak))){matches.add(mk);await saveCheckpoint();return true}if(SKIP.has(m.status)){log(`Omitiendo partido ${m.status}`);return 'SKIP'}if(!m.node)return 'SKIP';await tap(m.node,'Partido');let r=await until('match',7);if(!r){log('Partido no abre; se omite');return 'SKIP'}const local=val(first(r,'match-header-local-team-button')),away=val(first(r,'match-header-away-team-button'));if(!local||!away)throw new Error('Faltan botones de equipos en ficha');log(`PARTIDO: ${local} vs ${away}`);if(!await processTeam(category,competition,group,'match-header-local-team-button'))return false;if(!await processTeam(category,competition,group,'match-header-away-team-button'))return false;await back();if(!await until('competition'))return false;matches.add(mk);await saveCheckpoint();progress.matches++;log(`✓ PARTIDO COMPLETADO: ${local} vs ${away}`);return true}
function teamName(root){const card=first(root,'team-header-card');return clean(descendants(card).map(txt).find(v=>clean(v))||'')}
function rowName(node){const d=clean(desc(node));if(playerValid(d))return d;const items=descendants(node).map(n=>clean(txt(n))).filter(playerValid);return items.find(v=>v.includes(','))||items[0]||''}
function squadViewport(root){const sc=descendants(first(root,'team-plantilla-modal')).find(n=>n.getAttribute('class')==='android.widget.ScrollView'&&n.getAttribute('scrollable')==='true');const b=bounds(sc)||bounds(first(root,'team-plantilla-modal'));if(!b)return null;const nav=first(root,'android:id/navigationBarBackground'),n=bounds(nav);return [b[0],b[1],b[2],n&&n[1]>b[1]?Math.min(b[3],n[1]):b[3]]}
function rosterRows(root){const modal=first(root,'team-plantilla-modal');if(!modal)return {rows:[],trainer:false};const trainerNode=descendants(modal).find(n=>norm(txt(n))===norm('ENTRENADOR/A'));const trainerBounds=bounds(trainerNode),trainerY=trainerBounds?trainerBounds[1]:Infinity;const viewport=squadViewport(root);let rows=[];for(const n of descendants(modal)){if(n.getAttribute('clickable')!=='true'||n.getAttribute('resource-id'))continue;const b=bounds(n);if(!b||b[1]>=trainerY||!viewport)continue;const w=b[2]-b[0],h=b[3]-b[1],vw=viewport[2]-viewport[0];if(w<Math.min(700,vw*.68)||h<30||h>Math.max(260,(viewport[3]-viewport[1])*.25))continue;const name=rowName(n);rows.push({node:n,name,bounds:b})}rows.sort((a,b)=>a.bounds[1]-b.bounds[1]);for(let i=0;i<rows.length;i++){if(rows[i].name){rows[i].fingerprint='NAME:'+norm(rows[i].name);continue}const left=rows.slice(0,i).reverse().find(r=>r.name)?.name||'',right=rows.slice(i+1).find(r=>r.name)?.name||'';rows[i].fingerprint=`BETWEEN:${norm(left)}::${norm(right)}`}return {rows,trainer:!!trainerNode}}
function safePoint(root,row){const v=squadViewport(root),b=row.bounds;if(!v)return null;const left=Math.max(b[0],v[0]),right=Math.min(b[2],v[2]),top=Math.max(b[1],v[1])+18,bottom=Math.min(b[3],v[3])-18;return right-left>=120&&bottom-top>=34?[Math.round((left+right)/2),Math.round((top+bottom)/2)]:null}
async function scrollSquad(root,distance=170,direction='down'){const v=squadViewport(root);if(!v)throw new Error('No aparece ScrollView de plantilla');const [left,top,right,bottom]=v,h=bottom-top,x=Math.floor((left+right)/2),start=Math.min(bottom-100,top+Math.floor(h*.72)),dist=Math.max(90,Math.min(distance,h-190)),end=direction==='down'?Math.max(top+65,start-dist):Math.min(bottom-65,start+dist);await shell(`input swipe ${x} ${start} ${x} ${end} 270`);await sleep(160)}
async function firstPending(root,category,competition,group,team){let stagnant=0,last='',rescue=0;for(let k=0;k<260;k++){const {rows,trainer}=rosterRows(root);let recentered=false;for(const row of rows){const b=row.bounds,v=squadViewport(root),visible=v?Math.max(0,Math.min(b[3],v[3])-Math.max(b[1],v[1])):0;if(visible<24)continue;const player=row.name?key(category,competition,group,team,row.name):null;const anonkey=row.name?null:key(category,competition,group,team,'ANON',row.fingerprint);if(player&&players.has(player))continue;if(anonkey&&anonymous[anonkey]&&players.has(key(category,competition,group,team,anonymous[anonkey])))continue;const p=safePoint(root,row);if(p)return {row,root,anonkey};const delta=Math.round((b[1]+b[3])/2-(v[1]+v[3])/2);await scrollSquad(root,Math.max(140,Math.min(650,Math.abs(delta))),delta>0?'down':'up');root=await dump();recentered=true;break}if(recentered)continue;if(trainer)return {row:null,root,trainer:true};const sig=rows.map(r=>[r.fingerprint,Math.round(r.bounds[1]/8)]).join('|');stagnant=sig&&sig===last?stagnant+1:0;last=sig;if(stagnant>=3){await scrollSquad(root,600);rescue++;stagnant=0;if(rescue>=7)return {row:null,root,trainer:false}}else await scrollSquad(root,175);root=await dump()}return {row:null,root,trainer:false}}
function expectedCount(root){const modal=first(root,'team-plantilla-modal');if(!modal)return null;const label=descendants(modal).find(n=>norm(txt(n))===norm('JUGADORES/AS')),b=bounds(label);if(!b)return null;const num=descendants(modal).find(n=>/^\d+$/.test(clean(txt(n)))&&bounds(n)&&Math.abs(bounds(n)[1]-b[1])<90&&bounds(n)[0]>b[0]);return num?Number(txt(num)):null}
async function openSquad(root){const btn=first(root,'team-header-templates-button');if(!btn)throw new Error('Falta botón plantilla');await tap(btn,'Plantilla');return until('squad')}
async function closeSquad(root){const btn=first(root,'team-plantilla-close-button');if(btn)await tap(btn,'Cerrar plantilla');else await back();return until('team')}
async function backToSquad(){await back();let r=await until(['team','squad']);if(!r)return null;if(state(r)==='team')r=await openSquad(r);return r}
async function processTeam(category,competition,group,id){let root=await dump(),btn=first(root,id);if(!btn)return false;const hint=clean(desc(btn)),hintkey=key(category,competition,group,hint);if(teams.has(hintkey)){log(`Equipo ya completado: ${hint}`);return true}await tap(btn,'Equipo '+hint);root=await until('team');if(!root)return false;const name=teamName(root)||hint,tk=key(category,competition,group,name);if(teams.has(tk)){await back();return Boolean(await until('match'))}log(`EQUIPO: ${name}`);root=await openSquad(root);if(!root)return false;const expected=expectedCount(root);if(expected==null){log('No pude leer contador JUGADORES/AS, no marco equipo como completo');return false}let done=()=>[...players].filter(p=>p.startsWith(key(category,competition,group,name)+'|||')).length;showProgress('progress-team',name);showTeamCount(done(),expected);log(`La app indica ${expected} jugadores. Procesados ${done()}/${expected}`);let passes=0,errors=0;while(done()<expected){guard();const result=await firstPending(root,category,competition,group,name);root=result.root;if(result.row){const row=result.row,p=safePoint(root,row);if(!p){errors++;if(errors>10)return false;continue}await shell(`input tap ${p[0]} ${p[1]}`);root=await until('player');if(!root){log(`No abre jugador ${row.name||'(sin nombre)'}`);return false}const success=await processPlayer(category,competition,group,name,row.name,root,result.anonkey);root=await backToSquad();if(!root)return false;if(!success){errors++;if(errors>=12){log('Demasiados errores en plantilla, no la marcaré completa');return false}}else {errors=0;showTeamCount(done(),expected)}continue}if(done()>=expected)break;passes++;log(`Revisión plantilla ${passes}: ${done()}/${expected}`);if(passes>=10){log('Plantilla incompleta; checkpoint conservado');return false}let teamRoot=await closeSquad(root);if(!teamRoot)return false;root=await openSquad(teamRoot);if(!root)return false}const teamRoot=await closeSquad(root);if(!teamRoot)return false;teams.add(tk);teams.add(hintkey);await saveCheckpoint();progress.teams++;log(`✓ EQUIPO COMPLETADO: ${name} (${done()}/${expected})`);await back();return Boolean(await until('match'))}
// Lectura por temporada: mismas etiquetas y reglas de v13.
function header(root){return first(root,'player-header-card')}
function panel(root){return header(root)?.parentElement||null}
function playerIdentity(root){const h=header(root);if(!h)return {name:'',year:'',club:''};const items=descendants(h).map(n=>({value:clean(txt(n)),b:bounds(n)})).filter(o=>o.value&&o.b&&!/^\d{2}-\d{2}$/.test(o.value)&&!norm(o.value).includes('SEGUIDOR'));const birth=items.find(o=>/^(19|20)\d{2}$/.test(o.value));if(!birth)return {name:'',year:'',club:''};const cy=b=>Math.floor((b[1]+b[3])/2),y=cy(birth.b);const above=items.filter(o=>cy(o.b)<y&&y-cy(o.b)<=170&&playerValid(o.value)).sort((a,b)=>cy(b.b)-cy(a.b));const below=items.filter(o=>cy(o.b)>y&&cy(o.b)-y<=170&&!/^(19|20)\d{2}$/.test(o.value)&&!['PARTIDOS','TARJETAS','COMPETICIONES'].includes(norm(o.value))&&!norm(o.value).includes('SEGUIDOR')).sort((a,b)=>cy(a.b)-cy(b.b));return {name:above[0]?.value||'',year:birth.value,club:below[0]?.value||''}}
async function waitPlayer(root,attempts=8){for(let i=0;i<attempts;i++){if(state(root)==='player'){const id=playerIdentity(root);if(id.year||descendants(panel(root)).some(n=>txt(n)==='PARTIDOS'))return root}await sleep(230);root=await dump()}return null}
function textElems(root){return descendants(panel(root)).map(n=>({text:clean(txt(n)),b:bounds(n)})).filter(o=>o.text&&o.b)}
function stat(elems,label){const item=elems.find(o=>o.text===label);if(!item)return null;const x=Math.round((item.b[0]+item.b[2])/2),y=Math.round((item.b[1]+item.b[3])/2);const candidates=elems.filter(o=>/^\d+(?:[.,]\d+)?$/.test(o.text)).map(o=>({value:o.text,dx:Math.abs((o.b[0]+o.b[2])/2-x),dy:(o.b[1]+o.b[3])/2-y})).filter(o=>o.dx<=190&&o.dy>0&&o.dy<=220).sort((a,b)=>a.dx+a.dy-b.dx-b.dy);return candidates[0]?.value??null}
function readStats(root){const id=playerIdentity(root),elems=textElems(root),has=elems.some(e=>e.text==='PARTIDOS')&&elems.some(e=>e.text==='Convocados');const data={Jugador:id.name,Ano:id.year,Club:id.club,Estado:has?'OK':'SIN_ESTADISTICAS'};for(const label of ['Convocados','Titular','Suplente','Jugados','Total Goles','Media Goles','Amarillas','Rojas','Doble Amarilla'])data[label]=has?stat(elems,label):null;return data}
function compCards(root){return new Set(descendants(panel(root)).map(n=>clean(desc(n))).filter(d=>d&&norm(d).includes('POSICION')&&norm(d).includes('PUNTOS')))}
async function seasonData(root){const data=readStats(root);let cards=compCards(root);if(!cards.size){const p=panel(root)||first(root,'player-screen');if(p){await swipe(p,.82,.45,160);const r=await dump();cards=new Set([...cards,...compCards(r)])}}return {data,comps:[...cards].sort().join(' || ')}}
async function playerScrollTop(){const r=await dump(),p=panel(r)||first(r,'player-screen');if(p)await swipe(p,.25,.83,160)}
async function selectSeason(season){await playerScrollTop();let root=await dump();for(let attempt=0;attempt<6;attempt++){const h=header(root);if(!h)return null;const button=descendants(h).find(n=>val(n)===season&&n.getAttribute('clickable')==='true'&&bounds(n));if(button){await tap(button,`Temporada ${season}`);await sleep(180);return waitPlayer(await dump(),8)}const prev=descendants(h).find(n=>n.getAttribute('resource-id')==='day-slider-prev-button'&&n.getAttribute('enabled')==='true'&&bounds(n));if(!prev)return null;await tap(prev,'Temporadas anteriores');root=await dump()}return null}
function resolvedName(roster,results){if(playerValid(roster))return clean(roster);for(const s of ['25-26','24-25','26-27']){const v=results[s]?.data?.Jugador;if(playerValid(v))return clean(v)}return ''}
const numerics=new Set(['año_nacimiento','convocados','titular','suplente','jugados','total_goles','amarillas','rojas','doble_amarilla']);
function buildRow(category,competition,group,team,season,result,name,year){const d=result.data;const row={Temporada:season,Categoria_origen:category,Competicion_origen:competition,Grupo_origen:group||'',Equipo_origen:team,Jugador:name,Año_nacimiento:d.Ano||year,Club_en_temporada:d.Club||'',Estado:d.Estado||'',Convocados:d.Convocados,Titular:d.Titular,Suplente:d.Suplente,Jugados:d.Jugados,Total_Goles:d['Total Goles'],Media_Goles:d['Media Goles'],Amarillas:d.Amarillas,Rojas:d.Rojas,Doble_Amarilla:d['Doble Amarilla'],Competiciones_temporada:result.comps};const out={};for(const [original,sql] of Object.entries(FIELDS)){const val=row[original];if(numerics.has(sql))out[sql]=val==null||val===''?null:Number(String(val).replace(',','.'));else if(sql==='media_goles')out[sql]=val==null||val===''?null:Number(String(val).replace(',','.'));else out[sql]=val==null?'':String(val)}return out}
async function processPlayer(category,competition,group,team,roster,root,anonkey){root=await waitPlayer(root);if(!root){log('Ficha jugador no cargó');return false}const seasons={};seasons['26-27']=await seasonData(root);for(const s of ['25-26','24-25']){const historical=await selectSeason(s);if(historical)seasons[s]=await seasonData(historical);else seasons[s]=null}const name=resolvedName(roster,seasons);if(!name){log('Jugador sin nombre en 26-27/25-26/24-25; pendiente, no se marca completo');return false}const year=SEASONS.map(s=>seasons[s]?.data?.Ano).find(Boolean)||'',pk=key(category,competition,group,team,name);if(anonkey){anonymous[anonkey]=name;await saveCheckpoint()}if(players.has(pk))return true;let rows=0;for(const s of SEASONS){const result=seasons[s];if(!result)continue;result.data.Jugador=name;const row=buildRow(category,competition,group,team,s,result,name,year);await saveRow(row);rows++}if(!rows)return false;players.add(pk);await saveCheckpoint();progress.players++;log(`✓ JUGADOR: ${name} (${rows} temporadas)`);return true}
async function processTarget(target,requestedGroup){const alias=target.aliases.find(a=>groups.has(key(target.category,a,requestedGroup||'')));if(alias){log(`✓ Objetivo completo (checkpoint): ${target.category} | ${alias} | ${requestedGroup||'-'}`);return}const {competition,group}=await openTarget(target,requestedGroup);showProgress('progress-competition',competition);const groupKey=key(target.category,competition,group);if(groups.has(groupKey))return;log(`INICIANDO ${target.category} | ${competition} | ${group||'-'}`);let lastDay=0,ended=false;for(let day=1;day<=60;day++){guard();const result=await processMatchday(target.category,competition,group,day);if(result==='END'){ended=true;log(`✓ Fin de jornadas detectado en ${lastDay}`);break}if(result!==true)throw new Error(`No se pudo terminar jornada ${day}; checkpoint conservado`);lastDay=day}if(!ended)throw new Error('Límite de seguridad de 60 jornadas sin detectar final');groups.add(groupKey);await saveCheckpoint();log(`✓ OBJETIVO COMPLETADO: ${competition}`)}
async function startScraping(){
  // Una pausa manual NO debe cerrar WebUSB: conservar la sesión autenticada evita
  // iniciar dos lectores del mismo endpoint al reanudar.
  const reusedAdb=Boolean(adb);
  status(reusedAdb?'Reanudando sesión ADB…':'Conectando Android…');
  if(!reusedAdb){
    // Si una transferencia anterior nunca terminó, no abrir otra sesión encima.
    if(inFlightShell){
      await Promise.race([inFlightShell.catch(()=>{}),sleep(3500)]);
      if(inFlightShell)throw new Error('ADB_BUSY: existe una transferencia USB anterior aún activa. Desconecta y vuelve a conectar el móvil antes de reintentar.');
    }
    const manager=AdbDaemonWebUsbDeviceManager.BROWSER;
    if(!manager)throw new Error('WebUSB no disponible');
    const devices=await manager.getDevices();
    const backend=devices.length===1?devices[0]:await manager.requestDevice();
    if(!backend){status('Cancelado');return}
    const connection=await backend.connect();
    const connectedDevices=await navigator.usb.getDevices();
    activeUsbIdentity=connectedDevices.length===1?{vendorId:connectedDevices[0].vendorId,productId:connectedDevices[0].productId,serialNumber:connectedDevices[0].serialNumber}:null;
    adb=new Adb(await AdbDaemonTransport.authenticate({serial:backend.serial,connection,credentialStore:credentials}));
  }
  // Probar la sesión antes de iniciar navegación y clasificar errores WebUSB reales.
const model=await shell('getprop ro.product.model'),android=await shell('getprop ro.build.version.release');if(currentRunAutomatic)log('✓ Conexión recuperada. Continuando…');$('device').textContent=`Dispositivo: ${model} · Android ${android}`;log(`Conexión ADB: ${model} · Android ${android}`);initCheckpoint(resumeOnUsbReconnect&&confirmedCheckpoint?confirmedCheckpoint:args.checkpoint);log(args.write_enabled?'Persistencia Supabase activada (servidor Streamlit)':'ATENCIÓN: escritura desactivada; solo vista previa');await shell(`monkey -p ${PACKAGE} -c android.intent.category.LAUNCHER 1`);await sleep(800);for(const target of TARGETS){for(const group of target.groups){guard();status(`${target.category} → ${target.aliases[0]}${group?' / '+group:''}`);await processTarget(target,group)}}status('Actualización finalizada');log(`FIN: ${progress.players} jugadores · ${progress.teams} equipos · ${progress.matches} partidos nuevos en esta sesión`)}
// Streamlit v1 component protocol. Cada escritura espera confirmación del servidor antes de avanzar.
window.addEventListener('message',event=>{if(event.source!==window.parent)return;const msg=event.data;if(msg?.type!=='streamlit:render')return;args=msg.args||{};const ack=args.ack;if(pendingAck&&ack?.session===clientSession&&ack?.id===pendingAck.id){const pending=pendingAck;pendingAck=null;clearTimeout(pending.timer);if(ack.ok)pending.resolve();else pending.reject(new Error(ack.error||'Error al guardar en Supabase'));setTimeout(()=>{void flushDiagnostics()},0)}});
window.parent.postMessage({isStreamlitMessage:true,type:'streamlit:componentReady',apiVersion:1},'*');
// Ajustar la altura del iframe sin reiniciar el scraper ni recargar la página.
function resizeWebadbFrame(){
  const card=document.querySelector('.card');
  if(!card)return;
  const height=Math.max(340, Math.ceil(card.getBoundingClientRect().bottom)+12);
  window.parent.postMessage({isStreamlitMessage:true,type:'streamlit:setFrameHeight',height},'*');
}
const activityPanel=$('activity-details');
activityPanel?.addEventListener('toggle',()=>{
  const hint=activityPanel.querySelector('.log-hint');
  if(hint)hint.textContent=activityPanel.open?'Ocultar mensajes':'Ver mensajes';
  resizeWebadbFrame();
});
if(typeof ResizeObserver!=='undefined')new ResizeObserver(resizeWebadbFrame).observe(document.querySelector('.card'));
resizeWebadbFrame();
async function executeScraper(automatic=false){
  if(running || (automatic && pausedByUser))return;
  runGeneration++;cancelDisconnectCheck();
  lastRunStoppedByUser=false;
  currentRunAutomatic=automatic;
  running=true;
  cancelled=false;
  usbDisconnected=false;
  progress={players:0,teams:0,matches:0};
  draw();
  if(!automatic&&!resumeOnUsbReconnect)$('log').textContent='';
  // Confirmar la reconexión sólo después de autenticar ADB, no antes.
  else log('▶ Reanudando desde el último checkpoint confirmado…');
  try{
    await startScraping();
    resumeOnUsbReconnect=false;
  }catch(err){
    const message=String(err?.message||err);
    if(pausedByUser||cancelled||message==='STOP_REQUESTED'){
      lastRunStoppedByUser=true;
      resumeOnUsbReconnect=true;
      status('Pausado por el usuario');
      log('⏸ Actualización pausada. Se conserva el último checkpoint confirmado.');
    }else if(usbDisconnected||message==='USB_DISCONNECTED'){
      resumeOnUsbReconnect=true;
      status('Móvil desconectado · pausado');
      $('device').textContent='Conecta el móvil por USB y abre la app RFAF para continuar.';
      if(!technicalHistory.some(x=>x.endsWith('Móvil desconectado. Actualización pausada.')))log('⚠ Móvil desconectado. Actualización pausada.');
      await diagnostic('usb_disconnect',message.slice(0,180));
    }else{
      resumeOnUsbReconnect=true;
      status('Error');
      lastRecoveryFailureAt=Date.now();
      log('ERROR: '+message.slice(0,190));
      log('Consulta el diagnóstico del administrador.');
      await diagnostic('scraper_error',message.slice(0,400));
      log('Se conserva el último checkpoint confirmado.');
    }
  }finally{
    // Sólo una pausa VOLUNTARIA y sin daños USB retiene el transporte autenticado.
    // Tras error o desconexión se cierra, sin reutilizar sockets inválidos.
    if(!(pausedByUser && !usbDisconnected && lastRunStoppedByUser))await closeConnection();
    running=false;
    draw();
    if(resumeOnUsbReconnect&&!cancelled&&!pausedByUser&&usbReconnectPending){
      usbReconnectPending=false;
      scheduleReconnect();
    }
  }
}
async function scheduleReconnect(){
  if(reconnectAttempting||!resumeOnUsbReconnect||cancelled||pausedByUser)return;
  if(running){usbReconnectPending=true;return;}
  reconnectAttempting=true;
  try{
    await sleep(1200);
    if(!resumeOnUsbReconnect||running||cancelled||pausedByUser)return;
    const manager=AdbDaemonWebUsbDeviceManager.BROWSER;
    const devices=manager?await manager.getDevices():[];
    if(devices.length===1){
      status('Reconectando móvil…');
      await executeScraper(true);
    }else{
      // Chrome no ofrece permisos USB nuevos sin interacción del usuario.
      // Mostrar el botón SOLO cuando se detectó un dispositivo reconectado
      // pero no hay uno previamente autorizado.
      status('Autorización USB requerida');
      $('device').textContent='Conecta el móvil por USB, abre RFAF y autoriza el acceso USB.';
    }
  }catch(err){
    status('Reconexión pendiente');
    log('No se pudo recuperar la conexión. Abre la app RFAF y pulsa Actualizar jugadores.');
    await diagnostic('recovery_error',String(err?.message||err).slice(0,350));
  }finally{reconnectAttempting=false;}
}
navigator.usb?.addEventListener('connect',event=>{
  if(activeUsbIdentity && !sameActiveUsb(event.device))return;
  cancelDisconnectCheck();
  if(resumeOnUsbReconnect&&!cancelled&&!pausedByUser && (!activeUsbIdentity || sameActiveUsb(event.device))){
    void diagnostic('usb_reconnect','USB detectado de nuevo');
    scheduleReconnect();
  }
});
if(!window.isSecureContext||!navigator.usb||!AdbDaemonWebUsbDeviceManager.BROWSER){
  btn.disabled=true;status('WebUSB no disponible');log('Utiliza Chrome o Edge desde localhost o HTTPS');
}else btn.addEventListener('click',()=>{
  if(running){
    if(pausedByUser)return;
    // La pausa es cooperativa: permitir que el comando actual finalice.
    // Cancelar Promise.race dejaba un spawnAndWait activo que contaminaba el siguiente ADB.
    pausedByUser=true;
    usbReconnectPending=false;
    cancelDisconnectCheck();
    resumeOnUsbReconnect=true;
    status('Pausando…');
    draw();
    return;
  }
  if(lastRecoveryFailureAt && Date.now()-lastRecoveryFailureAt<5000){
    log('Espera unos segundos antes de volver a intentar la conexión.');
    return;
  }
  pausedByUser=false;
  cancelled=false;
  usbReconnectPending=false;
  cancelDisconnectCheck();
  // Si estaba pausado, se conserva el checkpoint confirmado para continuar.
  executeScraper(false);
});

$('usb-authorize')?.addEventListener('click',()=>{if(!running){resumeOnUsbReconnect=true;pausedByUser=false;void executeScraper(false)}});
