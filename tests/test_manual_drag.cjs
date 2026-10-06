const assert=require('node:assert/strict');
const {hit,translated}=require('../tools/live/manual-drag.js');
const project=x=>x.kind==='trend'?x.points.map(p=>({x:p[0],y:p[1]})):[{x:50,y:x.price}];
const unproject=p=>[Math.round(p.x),p.y];
const line={id:'a',kind:'trend',points:[[10,20],[100,80]]};
assert.equal(hit({x:10,y:20},[line],project).endpoint,0);
assert.equal(hit({x:103,y:82},[line],project).endpoint,1);
assert.equal(hit({x:55,y:50},[line],project).endpoint,null);
assert.equal(hit({x:55,y:150},[line],project),null);
assert.deepEqual(translated(line,null,{x:55,y:50},{x:65,y:60},project,unproject,false).points,[[20,30],[110,90]]);
assert.deepEqual(translated(line,1,{x:100,y:80},{x:110,y:95},project,unproject,false).points,[[10,20],[110,95]]);
assert.deepEqual(translated(line,null,{x:0,y:0},{x:10,y:3},project,unproject,true).points,[[20,20],[110,80]]);
assert.deepEqual(translated(line,0,{x:10,y:20},{x:100,y:20},project,unproject,false),null);
assert.equal(translated({id:'b',kind:'level',price:40,active:false},null,{x:50,y:42,lineY:40},{x:70,y:52},project,unproject,false).price,50);
assert.equal(translated({id:'b',kind:'level',price:40,active:false},null,{x:50,y:42,lineY:40},{x:70,y:52},project,unproject,false).active,false);
console.log('10 drawing geometry assertions passed');

