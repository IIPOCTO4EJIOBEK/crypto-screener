// Regression: a hidden tab, panel expansion and collapse must fit intrinsic height.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('tools/trade/page.py','utf8').split('TABS_JS = """<script>')[1].split('</script>"""')[0];
let bottom=2000,visible=true,resize,queue=[];
const events={},docEvents={},frameEvents={};
const doc={body:{},getElementById:()=>({getBoundingClientRect:()=>({bottom})}),
 documentElement:{style:{}},defaultView:{scrollY:0,getComputedStyle:()=>({paddingBottom:'20px'})},
 addEventListener:(n,cb)=>docEvents[n]=cb};
const frame={contentDocument:doc,style:{height:'600px'},get offsetWidth(){return visible?1200:0},
 getBoundingClientRect:()=>({height:parseFloat(frame.style.height)}),addEventListener:(n,cb)=>frameEvents[n]=cb};
const window={addEventListener:(n,cb)=>events[n]=cb,dispatchEvent:e=>events[e.type]?.(),ResizeObserver:true};
vm.runInNewContext(source,{window,document:{querySelectorAll:s=>s.includes('iframe')?[frame]:[]},
 location:{hash:''},Event:class{constructor(type){this.type=type}},
 ResizeObserver:class{constructor(cb){resize=cb}observe(){}disconnect(){}},
 requestAnimationFrame:cb=>queue.push(cb)});
const flush=()=>{while(queue.length)queue.shift()();};flush();
assert.equal(frame.style.height,'2020px');
bottom=3000;resize();flush();assert.equal(frame.style.height,'3020px');
bottom=400;docEvents.toggle();flush();assert.equal(frame.style.height,'420px');
visible=false;bottom=900;events.resize();flush();assert.equal(frame.style.height,'420px');
visible=true;events['bot-tab-change']();flush();assert.equal(frame.style.height,'920px');
assert.equal(doc.documentElement.style.overflowY,'hidden');
console.log('PASS: iframe expands, shrinks, defers hidden tabs and refits on activation');
