const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const buttons=[];let response={ok:true,items:[],events:[]},tones=0;
function el(tag){const e={style:{},dataset:{},classList:{toggle(){}},append(){},replaceChildren(){},querySelector(){return null},remove(){}};if(tag==='button')buttons.push(e);return e;}
class AudioContext{constructor(){this.state='suspended';this.currentTime=0;this.destination={};}async resume(){this.state='running';}createOscillator(){tones++;return {frequency:{},connect(){},start(){},stop(){}};}createGain(){return {gain:{setValueAtTime(){},exponentialRampToValueAtTime(){}},connect(){}};}}
const window={AudioContext};const context={window,document:{createElement:el,body:el(),head:el(),querySelectorAll(){return [];}},fetch:async()=>({ok:true,status:200,json:async()=>response}),setInterval(){},Set,Date,console};
vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'../tools/live/manual-levels.js'),'utf8'),context);
(async()=>{
 await window.manualLevelsApp.refresh();assert.equal(tones,0);
 await buttons[0].onclick();assert.equal(tones,3); // user gesture and diagnostic beep
 response={ok:true,items:[],events:[{id:1,symbol:'BTCUSDT',direction:'up',price:100,ts:1000,ack:false}]};
 await window.manualLevelsApp.refresh();assert.equal(tones,6);
 await window.manualLevelsApp.refresh();assert.equal(tones,6); // no replay on polling
 await buttons[0].onclick();response.events.push({id:2,symbol:'ETHUSDT',price:100,ts:1000,ack:false});
 await window.manualLevelsApp.refresh();assert.equal(tones,6); // mute respected
 console.log('Audio gesture, new level event, deduplication and mute passed; physical output not tested');
})().catch(e=>{console.error(e);process.exitCode=1;});
