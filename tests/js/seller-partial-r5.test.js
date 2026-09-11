'use strict';
const assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const source=fs.readFileSync('static/js/seller.js','utf8');
async function main(){
    const elements=new Map();
    const get=id=>{if(!elements.has(id)) elements.set(id,{hidden:false,textContent:'',style:{},setAttribute(){}});return elements.get(id);};
    const requests=[];
    const c={allShops:[{id:1},{id:2}],dashboardShopId:null,dashboardRequestId:0,shiftHistoryRequestId:0,doiSoatShopId:null,doiSoatBadgeRequestId:0,
        dashboardOrdersCache:null,dashboardStatsCache:null,
        chuoiThamSoDon:()=>'',chuoiThamSoNgay:()=>'',document:{getElementById:get,querySelectorAll:()=>[]},t:k=>k,
        capNhatBadgeDoiSoat(){},loadShiftHistory(){},showToast(){},renderDashboardOrders:(_,res)=>{get('orderList').textContent=String(res.orders[0].id);},renderDashboardStats:()=>{get('stats').textContent='loaded';},
        apiCall:async url=>{requests.push(url);if(url.includes('/stats'))throw Error('stats down');return {orders:[{id:92}]};}
    };
    vm.createContext(c);vm.runInContext(source.slice(source.indexOf('function khoaDashboardOrders('),source.indexOf('function renderShiftHistory(')),c);
    await c.loadDashboardShop(1);
    assert.equal(get('orderList').textContent,'92');
    assert.match(get('dashboardStatsStatus').textContent,/stats down/);
    assert.equal(get('dashboardStatsValues').hidden,true);
    assert.equal(get('dashboardStatsRetry').disabled,false);
    const count=requests.length;
    await c.loadDashboardShop(1,'stats');
    assert.equal(requests.length-count,1);
    const replies=[];c.apiCall=url=>new Promise(resolve=>replies.push({url,resolve}));
    const old=c.loadDashboardShop(1); const fresh=c.loadDashboardShop(2);
    replies.filter(x=>x.url.includes('/2')).forEach(x=>x.resolve({orders:[{id:200}]}));await fresh;
    replies.filter(x=>x.url.includes('/1')).forEach(x=>x.resolve({orders:[{id:100}]}));await old;
    assert.equal(get('orderList').textContent,'200');
    replies.length = 0;
    const both = c.loadDashboardShop(2);
    const retry = c.loadDashboardShop(2,'stats');
    replies.filter(x=>x.url.includes('/stats')).forEach(x=>x.resolve({orders:[]}));
    await retry;
    replies.find(x=>!x.url.includes('/stats')).resolve({orders:[{id:201}]});
    await both;
    assert.equal(get('orderList').textContent,'201','a regional retry must not invalidate its pending sibling');
    console.log('seller partial r5: passed');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
