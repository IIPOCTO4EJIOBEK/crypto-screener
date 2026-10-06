const assert=require('node:assert/strict');
class Element{constructor(){this.style={};this.children=[];this.listeners={};}append(...items){this.children.push(...items);}replaceChildren(...items){this.children=items;}addEventListener(name,fn){this.listeners[name]=fn;}getBoundingClientRect(){return {left:0,top:0};}setPointerCapture(){}releasePointerCapture(){}}
global.document=new Element();document.createElement=()=>new Element();global.setInterval=()=>0;
const {create}=require('../tools/live/manual-drag.js');
const base=1000000+10800;
const context={symbol:'USUSDT',tf:'5m',bars:Array.from({length:20},(_,i)=>[(1000000+i*300)*1000]),items:[{id:'a',symbol:'USUSDT',tf:'5m',kind:'level',price:40,active:false}],chart:{timeScale:()=>({width:()=>200,timeToCoordinate:t=>(t-base)/30,logicalToCoordinate:n=>n*10,coordinateToLogical:x=>x/10})},candle:{priceToCoordinate:p=>p,coordinateToPrice:y=>y}};
let saved=[];const box=new Element();const control=create(box,{context:()=>context,drawing:()=>false,preview:item=>{context.items[0]=item;},save:async item=>{saved.push(JSON.parse(JSON.stringify(item)));}});
const event=(x,y)=>({clientX:x,clientY:y,button:0,pointerId:1,target:{closest:()=>null},preventDefault(){},stopImmediatePropagation(){}});
(async()=>{
box.listeners.pointerdown(event(30,42));assert.equal(control.busy(),true);
box.listeners.pointermove(event(30,52));assert.equal(context.items[0].price,50);
box.listeners.pointerup(event(30,52));await new Promise(resolve=>setImmediate(resolve));
assert.equal(saved.length,1);assert.equal(saved[0].price,50);assert.equal(saved[0].active,false);assert.equal(control.busy(),false);
box.listeners.pointerdown(event(30,50));box.listeners.pointermove(event(30,0));assert.equal(context.items[0].price,50);box.listeners.pointerup(event(30,0));assert.equal(saved.length,1);
box.listeners.pointerdown(event(30,50));box.listeners.pointermove(event(30,60));
document.listeners.keydown({key:'Escape',preventDefault(){},stopImmediatePropagation(){}});
assert.equal(context.items[0].price,50);assert.equal(saved.length,1);
box.listeners.pointerdown(event(30,50));box.listeners.pointerup(event(30,50));assert.equal(saved.length,1);
context.items[0]={id:'a',symbol:'USUSDT',tf:'5m',kind:'trend',points:[[base+300,30],[base+1500,60]]};
box.listeners.pointerdown(event(50,60));box.listeners.pointermove(event(60,70));box.listeners.pointerup(event(60,70));await new Promise(resolve=>setImmediate(resolve));
assert.deepEqual(saved[1].points,[[base+300,30],[base+1800,70]]);
context.items[0]={id:'a',symbol:'USUSDT',tf:'5m',kind:'trend',points:[[base+300,30],[base+1500,60]]};
context.chart.timeScale=()=>({width:()=>200,timeToCoordinate:t=>(t-base)/30+20,logicalToCoordinate:n=>n*10,coordinateToLogical:x=>x/10});
box.listeners.pointerdown(event(70,60));box.listeners.pointermove(event(80,70));box.listeners.pointerup(event(80,70));await new Promise(resolve=>setImmediate(resolve));
assert.deepEqual(saved[2].points,[[base+300,30],[base+1800,70]]);
console.log('Pointer drag persistence, cancellation, click-only and endpoint adjustment passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
