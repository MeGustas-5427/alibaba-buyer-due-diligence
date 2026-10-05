// Fully invented browser responses. No network, account or cookie store access.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '../scripts/collection');
const source = fs.readFileSync(path.join(root, 'list.browser.js'), 'utf8');
const options = {startDate: '2026-10-04', endDate: '2026-10-05', salesName: '全部'};
const rows = n => Array.from({length:n}, (_,i) => ({customerId:'synthetic-'+i}));
async function run(fetch, config=options) {
  const context = {window:{}, location:{hostname:'alicrm.alibaba.com',pathname:'/'},
    document:{cookie:'_tb_token_=SYNTHETIC'}, URL, Date, AbortSignal, console, fetch};
  vm.runInNewContext(source, context);
  return context.window.collectAliCrmCustomerList(config);
}
const response = (total, data) => ({ok:true,status:200,redirected:false,json:async()=>({success:true,data:{total,data}})});
(async()=>{
  const probeSource=fs.readFileSync(path.join(root,'page_probe.browser.js'),'utf8');
  const probe=location=>vm.runInNewContext('('+probeSource+')',{location,document:{readyState:'complete'}})();
  const page={hostname:'i.alibaba.com',pathname:'/hub/alicrm/',protocol:'https:',port:'',href:'PRIVATE',search:'?private=value'};
  assert.equal(probe(page).allowedPage,true);
  assert.equal(probe({...page,hostname:'login.alibaba.com'}).allowedPage,false);
  assert.equal(probe({...page,hostname:'alicrm.alibaba.com.evil.example'}).allowedPage,false);
  assert.equal(probe({...page,port:'8443'}).allowedPage,false);
  assert.equal(probe({...page,hostname:'profile.alibaba.com',pathname:'/profile/my_profile.htm'}).profilePage,true);
  assert.equal(JSON.stringify(probe(page)).includes('PRIVATE'),false);
  assert.equal(JSON.stringify(probe(page)).includes('private=value'),false);
  assert.match(probe(page).sessionState,/not_verified/);
  const all = rows(501);
  let requests = 0;
  let result = await run(async (_url, init) => {
    requests++;
    const p = JSON.parse(init.body);
    return response(501, all.slice((p.pageNum-1)*p.pageSize,p.pageNum*p.pageSize));
  });
  assert.equal(requests,2);
  assert.equal(result.criteria.recordsCollected,501);
  result = await run(async()=>response(0,[]));
  assert.equal(result.criteria.total,0);
  let sizes=[];
  result = await run(async (_url, init)=>{
    const p=JSON.parse(init.body); sizes.push(p.pageSize);
    if(p.pageSize===500) return {ok:false,status:413,json:async()=>({success:false,message:'pageSize rejected'})};
    return response(501,all.slice((p.pageNum-1)*100,p.pageNum*100));
  });
  assert.deepEqual(sizes,[500,100,100,100,100,100,100]);
  requests=0;
  await assert.rejects(run(async()=>{
    requests++; return {ok:false,status:403,json:async()=>({success:false})};
  }),/authorization/);
  assert.equal(requests,1); // Auth failure must not trigger smaller-page retry.
  await assert.rejects(run(async()=>response(undefined,[])),/Invalid/);
  await assert.rejects(run(async()=>response(2,[{customerId:'same'},{customerId:'same'}])),/duplicate/);
  await assert.rejects(run(async()=>response(5001,all.slice(0,500))),/5000/);
  const large = rows(5001);
  const segments = [{name:'SYNTHETIC assigned',salesId:'synthetic-owner'},{name:'SYNTHETIC unassigned',salesId:'synthetic-unassigned'}];
  const splitFetch = async (_url, init) => {
    const p = JSON.parse(init.body);
    const id = JSON.parse(p.jsonArray).find(f=>f.id==='669')?.sales_id;
    const selected = id === 'synthetic-owner' ? large.slice(0,3000) : id === 'synthetic-unassigned' ? large.slice(3000) : large;
    return response(selected.length, selected.slice((p.pageNum-1)*p.pageSize,p.pageNum*p.pageSize));
  };
  result = await run(splitFetch, {...options,salesSegments:segments});
  assert.equal(result.criteria.total,5001);
  assert.equal(result.segments.length,2);
  await assert.rejects(run(splitFetch, {...options,salesSegments:segments.slice(0,1)}),/coverage/);
  requests=0;
  await assert.rejects(run(async()=>response(++requests===1?501:502,all.slice(0,500))),/changing/);
  const profile=require(path.join(root,'profile.browser.js'));
  const fixture={personalInfo:{firstName:'Synthetic \\"quoted\\" name'},companyInfo:{compName:'SYNTHETIC \\ Company'},array:[{x:1}]};
  assert.deepEqual(profile.parsePageData('<script>window._PAGE_DATA='+JSON.stringify(fixture)+';</script>'),fixture);
  assert.throws(()=>profile.parsePageData('<script>window._PAGE_DATA={a:(()=>{throw Error("unsafe")})()};</script>'));
  assert.throws(()=>profile.normalizeProfileUrl('https://profile.alibaba.com.evil.example/profile/myprofile.htm?m=x'));
  console.log('Collection browser offline checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
