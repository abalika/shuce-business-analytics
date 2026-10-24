/* Browser-only drafts. Saved server snapshots remain the source of exports. */
(function(root){
  class ReviewDrafts {
    constructor(storage){this.storage=storage;this.available=true;this.prefix='shuce.draft.v1.';}
    read(key){try{const raw=this.storage.getItem(this.prefix+key);if(!raw)return null;const r=JSON.parse(raw);return r?.version===1&&r.payload&&typeof r.payload==='object'&&!Array.isArray(r.payload)?r:null;}catch{this.available=false;return null;}}
    write(key,payload){try{this.storage.setItem(this.prefix+key,JSON.stringify({version:1,saved_at:new Date().toISOString(),payload}));return true;}catch{this.available=false;return false;}}
    remove(key){try{this.storage.removeItem(this.prefix+key);return true;}catch{this.available=false;return false;}}
    notes(id){return id?this.read('notes.'+id):null;}
    saveNotes(id,notes){return id?this.write('notes.'+id,notes):false;}
    clearNotes(id){return id?this.remove('notes.'+id):false;}
    task(){return this.read('task');}
    saveTask(review_id,task){return this.write('task',{review_id,task});}
    clearTask(){return this.remove('task');}
  }
  if(typeof module!=='undefined'&&module.exports)module.exports=ReviewDrafts;
  else root.ReviewDrafts=ReviewDrafts;
})(globalThis);
