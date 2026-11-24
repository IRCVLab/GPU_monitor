// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import {getFlowState,evaluateGraph} from './api.ts';
test('live requests explicitly skip historical peaks and support cancellation',async()=>{
 const original=globalThis.fetch;const calls=[];
 globalThis.fetch=async(url,options)=>{calls.push({url,options});return new Response('{}',{status:200});};
 try{const controller=new AbortController();await getFlowState('diagram',24,undefined,{includePeaks:false,signal:controller.signal});assert.equal(new URL(calls[0].url,'http://local').searchParams.get('include_peaks'),'false');assert(calls[0].options.signal);await evaluateGraph({nodes:[],edges:[],settings:{}},24,undefined,{includePeaks:false});assert.equal(JSON.parse(calls[1].options.body).include_peaks,false);}finally{globalThis.fetch=original;}
});
