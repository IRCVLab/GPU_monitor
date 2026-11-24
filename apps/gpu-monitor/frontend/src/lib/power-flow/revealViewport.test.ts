// @ts-nocheck
import test from 'node:test';import assert from 'node:assert/strict';import {revealViewport} from './revealViewport.ts';
test('opening an inspector reveals an obscured selected path without reducing zoom',()=>{assert.deepEqual(revealViewport({x:0,y:0,zoom:.8},{x:1200,y:40,width:420,height:850},{x:1400,y:600,width:220,height:112},1100,960),{x:-220,y:0,zoom:.8});});
test('selection already visible does not move the viewport',()=>{assert.deepEqual(revealViewport({x:20,y:30,zoom:1},{x:30,y:30,width:500,height:600},{x:40,y:40,width:220,height:112},1100,960),{x:20,y:30,zoom:1});});
test('a path larger than the viewport keeps the selected component visible at the same zoom',()=>{const next=revealViewport({x:0,y:0,zoom:1},{x:0,y:0,width:1700,height:1400},{x:1400,y:1100,width:220,height:112},1100,960);assert.equal(next.zoom,1);assert(next.x+1400>=24&&next.x+1620<=1076);assert(next.y+1100>=24&&next.y+1212<=936);});
