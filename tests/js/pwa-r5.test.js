'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
async function main(){
    const listeners=new Map();
    const node=()=>({children:[],hidden:false,addEventListener(name,fn){this[name]=fn;},setAttribute(){},append(...items){this.children.push(...items);},appendChild(item){this.children.push(item);},style:{}});
    const slots=[node(),node()];
    const c={console,navigator:{},localStorage:{getItem(){throw Error('private');},setItem(){},removeItem(){}},document:{readyState:'complete',body:node(),createElement:node,querySelectorAll:()=>slots},matchMedia:()=>({matches:false}),addEventListener:(name,fn)=>listeners.set(name,fn)};
    c.window=c;vm.createContext(c);vm.runInContext(fs.readFileSync('static/js/pwa.js','utf8'),c);
    assert.equal(slots[0].children.length,1,'install help must exist even without an install event');
    let prompts=0;listeners.get('beforeinstallprompt')({preventDefault(){},prompt(){prompts++;},userChoice:Promise.resolve({outcome:'dismissed'})});
    assert.equal(prompts,0);assert.equal(c.FSellingPWA.caiDat(),true);assert.equal(c.FSellingPWA.caiDat(),false);await Promise.resolve();await Promise.resolve();assert.equal(prompts,1);assert.equal(slots[0].children.length,1);
    const handlers=new Map(),deleted=[],added=[];let force=0,claim=0,matched=[];
    const worker={URL,Response,console,self:{location:{origin:'http://localhost'},addEventListener:(n,f)=>handlers.set(n,f),skipWaiting:()=>force++,clients:{claim:()=>claim++}},
        caches:{open:async()=>({add:async url=>added.push(url),put:async()=>{}}),keys:async()=>['fselling-vo-old','another-app'],delete:async key=>deleted.push(key),match:async key=>{matched.push(key);return key==='/offline.html'?new Response('offline shell'):undefined;}},fetch:async()=>{throw Error('down');}};
    vm.createContext(worker);vm.runInContext(fs.readFileSync('static/sw.js','utf8'),worker);
    let waiting;handlers.get('install')({waitUntil:p=>waiting=p});await waiting;assert.equal(force,0);
    for (const route of ['/fnb','/fnb/station/kitchen','/fnb/station/bar']) assert.ok(added.includes(route));
    handlers.get('activate')({waitUntil:p=>waiting=p});await waiting;assert.equal(claim,0);assert.deepEqual(deleted,['fselling-vo-old']);assert.ok(added.includes('/offline.html'));
    for(const [url,method] of [['/api/orders','GET'],['/orders','POST']]){let intercepted=false;handlers.get('fetch')({request:{url:'http://localhost'+url,method},respondWith:()=>intercepted=true});assert.equal(intercepted,false);}
    handlers.get('fetch')({request:{url:'http://localhost/fnb',method:'GET',mode:'navigate'},respondWith:p=>waiting=p});assert.equal(await (await waiting).text(),'offline shell');
    matched=[];worker.fetch=async()=>new Response('new asset');handlers.get('fetch')({request:{url:'http://localhost/js/api.js?v=new',method:'GET'},respondWith:p=>waiting=p});assert.equal(await (await waiting).text(),'new asset');assert.equal(matched[0].url,'http://localhost/js/api.js?v=new');
    console.log('pwa r5: passed');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
