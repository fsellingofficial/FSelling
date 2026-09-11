'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source=fs.readFileSync('static/js/pos.js','utf8');
const section=(from,to)=>source.slice(source.indexOf(from),source.indexOf(to,source.indexOf(from)));
async function main() {
    const bodies=[]; let saved; let offlineWrites=0;
    const c={ checkoutBusy:false,checkoutOperationId:null,currentOrderId:null,
        window:{OfflineBan:{}},OfflineBan:{dangOffline:()=>true},cart:[{}],cashTenderedAmount:100,
        capNhatNutCheckout(){},showToast(){},dich:k=>k,
        document:{getElementById:()=>null},
        luuCheckoutDangDo:s=>{saved=s;},xoaCheckoutDangDo:()=>{saved=null;},
        luuBanOffline:()=>{offlineWrites++;},
        guiYeuCauTaoDonDangDo:async state=>{ bodies.push(structuredClone(state.create_payload)); throw Object.assign(Error('lost'),{status:401,mutationOutcomeUnknown:true}); }
    };
    vm.createContext(c);
    vm.runInContext(section('function laLoi4xx(', 'function currentShopHasTransferAccount(')+section('async function thuTaoDonDangDo(', 'async function checkout('),c);
    assert.equal(c.laLoi4xx({status:401,mutationOutcomeUnknown:true}),false);
    const state={phase:'creating',operation_id:'same',create_payload:{operation_id:'same',items:[{product_id:1,quantity:1}]},payment_method:'cash',total:100};
    await c.thuTaoDonDangDo(state);
    assert.equal(saved,state);
    assert.equal(offlineWrites,0,'a sent request with unknown outcome must not become a second offline sale');
    await c.thuTaoDonDangDo(saved);
    assert.deepEqual(bodies[0],bodies[1]);
    let opened=false;
    const receiptContext={posMobileCartMedia:{matches:true},cart:[],duLieuHoaDonHienTai:{id:93},
        document:{body:{classList:{add(){}}},getElementById:id=>({style:{display:'block'},hidden:true,removeAttribute(){},setAttribute(){},focus(){},classList:{add(){if(id==='posCheckoutColumn')opened=true;}}})}};
    vm.createContext(receiptContext);
    vm.runInContext(section('function moGioHangMobile(', 'function capNhatGioHangResponsivePOS('),receiptContext);
    receiptContext.moGioHangMobile();
    assert.equal(opened,true,'paid receipt must open even after cart has been reset');
    const payBodies=[];
    const payContext={currentOrderId:null,pendingCashOrderId:null,total:5000,cashTenderedAmount:10000,
        capNhatTienKhachDua(){},luuCheckoutDangDo(){},xacNhanTongTienServer:async()=>true,
        posRequestUi:()=>({}),laLoi4xx:e=>e.status>=400&&e.status<500&&!e.mutationOutcomeUnknown,
        apiCall:async(_path,_method,body)=>{payBodies.push(structuredClone(body));throw Object.assign(Error('lost'),{status:503,mutationOutcomeUnknown:true});},
        dich:k=>k,document:{getElementById:()=>null},showToast(){},dinhDangTien:v=>v};
    vm.createContext(payContext);
    vm.runInContext(section('async function hoanTatTienMatDangCho(', 'function apDungKetQuaDiemServer('),payContext);
    const cashState={order_id:96,server_total:5000};
    await assert.rejects(payContext.hoanTatTienMatDangCho(cashState));
    payContext.cashTenderedAmount=20000;
    const restored=JSON.parse(JSON.stringify(cashState));
    await assert.rejects(payContext.hoanTatTienMatDangCho(restored));
    assert.deepEqual(payBodies,[{tendered_amount:10000},{tendered_amount:10000}]);
    payContext.apiCall=async()=>{throw Object.assign(Error('short'),{status:400});};
    await assert.rejects(payContext.hoanTatTienMatDangCho(restored));
    assert.equal(restored.cash_pay_payload,undefined,'definitive validation failure permits correction');
    console.log('pos request r5: passed');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
