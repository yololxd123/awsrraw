// Exercise the actual promotion locator with visible/hidden/obscured chooser fixtures.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const text=fs.readFileSync(require('path').join(__dirname,'../extension/mainListener.js'),'utf8');
assert(!text.includes('dispatchEvent('));
const fn=text.slice(text.indexOf('  function promotionGeometry('),text.lastIndexOf('})();'));
let items=[],hit=null;
const context={findBoardElement:()=>({getBoundingClientRect:()=>({left:100,top:100,width:800,height:800})}),
 document:{querySelectorAll:()=>items,elementFromPoint:()=>hit}};
vm.createContext(context);vm.runInContext(fn,context);
function piece(letter,width=80){return {dataset:{piece:letter},className:'promotion-piece',textContent:'',contains:x=>false,
 getBoundingClientRect:()=>({left:500,top:100,width,height:80})};}
items=[piece('q',0),piece('n')];hit=items[1];
assert.equal(context.promotionGeometry('q').ok,false);
assert.equal(context.promotionGeometry('n').ok,true);
assert.equal(context.promotionGeometry('n').x,.55);
hit={};assert.equal(context.promotionGeometry('n').ok,false);
assert.equal(context.promotionGeometry('x').ok,false);
console.log('PASS visible promotion locator, underpromotion, obstruction, invalid piece; no DOM input dispatch');
