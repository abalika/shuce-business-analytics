const assert=require('node:assert/strict');
const ReviewDrafts=require('../web/review-drafts.js');
const data=new Map();
const storage={getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k)};
let passed=0;
function check(name,fn){fn();passed++;console.log('PASS '+name);}
const d=new ReviewDrafts(storage);
check('notes stay attached to their review',()=>{d.saveNotes('A',{facts_note:'甲'});d.saveNotes('B',{facts_note:'乙'});assert.equal(d.notes('A').payload.facts_note,'甲');assert.equal(d.notes('B').payload.facts_note,'乙');});
check('new instance recovers persisted notes',()=>assert.equal(new ReviewDrafts(storage).notes('A').payload.facts_note,'甲'));
check('clearing one draft keeps the other',()=>{d.clearNotes('A');assert.equal(d.notes('A'),null);assert.equal(d.notes('B').payload.facts_note,'乙');});
check('next-cycle draft keeps base review and declaration',()=>{d.saveTask('B',{start:'2026-09-28',confirmed_complete:false,previous_review_id:'B'});const t=new ReviewDrafts(storage).task().payload;assert.equal(t.review_id,'B');assert.equal(t.task.start,'2026-09-28');assert.equal(t.task.confirmed_complete,false);});
check('task can be cleared separately',()=>{d.clearTask();assert.equal(d.task(),null);assert.ok(d.notes('B'));});
check('malformed stored data is not restored',()=>{storage.setItem(d.prefix+'notes.X','bad json');assert.equal(d.notes('X'),null);});
check('storage failure is exposed without throwing',()=>{const broken=new ReviewDrafts({getItem(){throw Error('denied');},setItem(){throw Error('full');}});assert.equal(broken.notes('A'),null);assert.equal(broken.saveNotes('A',{}),false);assert.equal(broken.available,false);});
console.log(JSON.stringify({passed}));
